import os
import unittest

from helpers import FakeEnv
from roles import team as teammod
from roles.state import Store


class TeamTests(FakeEnv):
    def up(self, **kw):
        return teammod.team_up(self.h, self.store, self.roles, self.team, "w1:p1", self.settings, **kw)

    def test_expand_slots_follows_placement_hints(self):
        s = {x.role: x for x in teammod.expand_slots(self.team)}
        self.assertTrue(s["planner"].lead)
        self.assertEqual((s["implementer"].anchor, s["implementer"].direction), (("planner", 1), "right"))
        self.assertEqual((s["reviewer"].anchor, s["reviewer"].direction), (("implementer", 1), "down"))
        self.assertEqual((s["tester"].anchor, s["tester"].direction), (("planner", 1), "down"))
        self.assertEqual((s["runner"].anchor, s["runner"].direction), (("tester", 1), "down"))

    def test_team_up_spawns_everything_but_the_lead(self):
        rep = self.up()
        panes = self.fake()["panes"]
        self.assertEqual(len(panes), 5)                      # lead + 4 new
        self.assertEqual(len(rep["spawned"]), 4)
        self.assertEqual(rep["failed"], [])
        data = self.store.load("w1")
        self.assertEqual({p["role"] for p in data["panes"].values()},
                         {"planner", "implementer", "reviewer", "tester", "runner"})
        self.assertTrue(data["panes"]["w1:p1"]["lead"])
        spawned = [p for p in data["panes"].values() if p["spawned_by"] == "team-up"]
        self.assertEqual(len(spawned), 4)
        self.assertTrue(all(p["status"] == "ready" for p in data["panes"].values()))
        kinds = {data["panes"][pid]["role"]: panes[pid]["agent"] for pid in data["panes"]}
        self.assertEqual(kinds["implementer"], "codex")
        self.assertEqual(kinds["reviewer"], "claude")
        self.assertIsNone(kinds["runner"])                   # shell role: no agent
        # every tracked pane got display metadata
        self.assertTrue(all(panes[pid]["meta"].get("title", "").startswith("[") for pid in data["panes"]))

    def test_team_up_places_panes_relative_to_their_anchor(self):
        self.up()
        by_new = {x["new"]: x for x in self.fake()["splits"]}
        data = self.store.load("w1")
        impl, rev = self.pane_of("implementer", data), self.pane_of("reviewer", data)
        self.assertEqual((by_new[impl]["target"], by_new[impl]["direction"]), ("w1:p1", "right"))
        self.assertEqual((by_new[rev]["target"], by_new[rev]["direction"]), (impl, "down"))

    def test_roster_goes_to_the_lead(self):
        rep = self.up()
        self.assertEqual(rep["roster_state"], "sent")
        text = self.prompts("w1:p1")[-1]
        self.assertIn("팀 'dev'", text)
        self.assertIn("implementer", text)
        self.assertIn("roles send --role", text)

    def test_roster_describes_roles_with_their_whole_prompt(self):
        self.roles["reviewer"].prompt = "첫 줄입니다.\n  둘째 줄입니다.\n셋째 줄입니다."
        text = self.up()["roster"]
        self.assertIn("첫 줄입니다. 둘째 줄입니다. 셋째 줄입니다.", text)       # not cut after the first line
        self.assertIn("Runner", [l for l in text.splitlines() if "runner" in l and "w1:" in l][0])   # no prompt -> label

    def test_roster_is_deferred_while_lead_is_working_then_flushed(self):
        self.set_status("w1:p1", "working")
        rep = self.up()
        self.assertEqual(rep["roster_state"], "deferred")
        self.assertEqual(self.prompts("w1:p1"), [])
        self.assertIsNotNone(self.store.load("w1")["pending_roster"])
        self.assertTrue(teammod.flush_roster(self.h, self.store, "w1", "w1:p1", "idle"))
        self.assertIn("팀 'dev'", self.prompts("w1:p1")[-1])
        self.assertIsNone(self.store.load("w1")["pending_roster"])

    def test_lead_that_is_a_plain_shell_gets_no_injection(self):
        self.edit_fake(lambda s: s["panes"]["w1:p1"].update(agent=None))
        rep = self.up()
        self.assertEqual(rep["roster_state"], "shell")
        self.assertIn("팀 'dev'", rep["roster"])

    def test_team_up_is_idempotent(self):
        self.up()
        splits_before = len(self.fake()["splits"])
        prompts_before = len(self.prompts("w1:p1"))
        rep = self.up()
        self.assertEqual(rep["plan"], [])
        self.assertIsNone(rep["roster_state"])                         # no repeat roster on a no-op run
        self.assertEqual(len(self.prompts("w1:p1")), prompts_before)
        self.assertEqual(len(self.fake()["splits"]), splits_before)
        self.assertEqual(len(self.fake()["panes"]), 5)

    def test_dry_run_changes_nothing(self):
        rep = self.up(dry_run=True)
        self.assertEqual(len(rep["plan"]), 4)
        self.assertEqual(len(self.fake()["panes"]), 1)
        self.assertEqual(self.fake()["splits"], [])
        self.assertFalse(self.store.exists("w1"))

    def test_closed_member_is_recreated_alone(self):
        self.up()
        victim = self.pane_of("reviewer")
        self.edit_fake(lambda s: s["panes"].pop(victim))
        before = len(self.fake()["splits"])
        rep = self.up()
        self.assertEqual([p["role"] for p in rep["plan"]], ["reviewer"])
        self.assertEqual(len(self.fake()["splits"]), before + 1)
        self.assertEqual(len(self.fake()["panes"]), 5)

    def test_failed_agent_start_is_isolated_and_retried(self):
        self.edit_fake(lambda s: s.update(bad_kinds=["codex"]))
        rep = self.up()
        data = self.store.load("w1")
        impl = self.pane_of("implementer", data)
        self.assertEqual(data["panes"][impl]["status"], "failed")
        self.assertIn("unsupported", data["panes"][impl]["error"])
        self.assertEqual(sum(1 for p in data["panes"].values() if p["status"] == "ready"), 4)
        self.assertEqual(len(self.fake()["panes"]), 5)       # failed member keeps its pane
        self.assertTrue(any("일부 실패" in n["title"] for n in self.fake()["notifications"]))
        # fix the problem, re-run: the same pane is retried, nothing new is split
        self.edit_fake(lambda s: s.update(bad_kinds=[]))
        splits = len(self.fake()["splits"])
        rep = self.up()
        self.assertEqual(len(self.fake()["splits"]), splits)
        self.assertEqual(self.store.load("w1")["panes"][impl]["status"], "ready")
        self.assertEqual(self.fake()["panes"][impl]["agent"], "codex")

    def test_agents_lost_in_a_restart_are_started_again(self):
        self.up()
        impl, runner = self.pane_of("implementer"), self.pane_of("runner")
        # a herdr restart restores panes, not the agent processes inside them
        self.edit_fake(lambda s: [s["panes"][p].update(agent=None, agent_status="unknown") for p in (impl, self.pane_of("reviewer"))])
        splits = len(self.fake()["splits"])
        rep = self.up()
        self.assertEqual(sorted(p["role"] for p in rep["plan"]), ["implementer", "reviewer"])   # runner has no agent
        self.assertEqual(len(self.fake()["splits"]), splits)                                       # no new panes
        self.assertEqual(self.fake()["panes"][impl]["agent"], "codex")
        self.assertIsNone(self.fake()["panes"][runner]["agent"])

    def test_prompt_never_types_into_a_pane_whose_agent_is_gone(self):
        # A restored pane whose agent died is a plain shell: a refused prompt must stay an error, because typing
        # the payload there would execute it.
        from roles.herdr import HerdrError
        self.edit_fake(lambda s: s["panes"]["w1:p1"].update(unnamed=True))
        with self.assertRaises(HerdrError):
            self.h.agent_prompt("w1:p1", "rm -rf /tmp/nothing")
        pane = self.fake()["panes"]["w1:p1"]
        self.assertNotIn("typed", pane)
        self.assertEqual(pane["prompts"], [])

    def test_failed_roster_delivery_is_deferred_not_lost(self):
        self.edit_fake(lambda s: s["panes"]["w1:p1"].update(unnamed=True))
        rep = self.up()
        self.assertEqual(rep["roster_state"], "deferred")
        self.assertIsNotNone(self.store.load("w1")["pending_roster"])
        self.assertNotIn("typed", self.fake()["panes"]["w1:p1"])

    def test_split_failure_is_reported_and_others_continue(self):
        self.edit_fake(lambda s: s.update(fail_split_after=1))
        rep = self.up()
        self.assertEqual(len(rep["spawned"]), 1)
        self.assertEqual(len(rep["failed"]), 3)

    def test_equal_share_ratios_for_a_group(self):
        self.write_team = os.path.join(self.cfg, "teams", "wide.toml")
        with open(self.write_team, "w") as f:
            f.write('[[member]]\nrole = "planner"\nlead = true\n'
                    '[[member]]\nrole = "tester"\ncount = 3\nsplit = "down"\n')
        from roles import config
        team = config.load_team("wide", self.roles)
        teammod.team_up(self.h, self.store, self.roles, team, "w1:p1", self.settings)
        splits = self.fake()["splits"]
        self.assertEqual(len(splits), 3)
        self.assertEqual([round(x["ratio"], 4) for x in splits], [0.25, 0.3333, 0.5])
        # each split targets the previously created pane: no repeated splitting of the same pane
        self.assertEqual([x["target"] for x in splits], ["w1:p1", splits[0]["new"], splits[1]["new"]])

    def test_explicit_ratio_overrides_the_computed_one(self):
        with open(os.path.join(self.cfg, "teams", "r.toml"), "w") as f:
            f.write('[[member]]\nrole = "planner"\nlead = true\n[[member]]\nrole = "tester"\nratio = 0.7\n')
        from roles import config
        teammod.team_up(self.h, self.store, self.roles, config.load_team("r", self.roles), "w1:p1", self.settings)
        self.assertEqual(self.fake()["splits"][0]["ratio"], 0.7)

    def test_max_spawn_guard(self):
        self.settings["max_spawn"] = 2
        with self.assertRaisesRegex(teammod.TeamError, "max_spawn"):
            self.up()
        self.assertEqual(len(self.fake()["panes"]), 1)

    def test_team_down_only_closes_what_it_spawned(self):
        self.edit_fake(lambda s: s["panes"].update({"w1:p9": self._pane("w1:p9")}))   # user-made pane
        self.up()
        res = teammod.team_down(self.h, self.store, "w1")
        self.assertEqual(len(res["closed"]), 4)
        panes = self.fake()["panes"]
        self.assertEqual(sorted(panes), ["w1:p1", "w1:p9"])
        data = self.store.load("w1")
        self.assertIsNone(data["team"])
        self.assertEqual(list(data["panes"]), ["w1:p1"])

    def test_team_status_reports_missing_and_gone(self):
        self.up()
        victim = self.pane_of("tester")
        self.edit_fake(lambda s: s["panes"].pop(victim))
        rows = {r["role"]: r for r in teammod.team_status(self.h, self.store, self.roles, "w1", self.team)["rows"]}
        self.assertEqual(rows["tester"]["state"], "gone")
        self.assertEqual(rows["planner"]["state"], "ready")

    def test_assign_and_unassign(self):
        self.edit_fake(lambda s: s["panes"].update({"w1:p7": self._pane("w1:p7")}))
        teammod.assign(self.h, self.store, self.roles, "w1", "w1:p7", "reviewer")
        self.assertEqual(self.store.load("w1")["panes"]["w1:p7"]["role"], "reviewer")
        self.assertIn("Reviewer", self.fake()["panes"]["w1:p7"]["meta"]["title"])
        with self.assertRaises(teammod.TeamError):
            teammod.assign(self.h, self.store, self.roles, "w1", "w1:p7", "wizard")
        self.assertTrue(teammod.unassign(self.h, self.store, "w1", "w1:p7"))
        self.assertNotIn("w1:p7", self.store.load("w1")["panes"])

    def test_state_is_keyed_by_session(self):
        a, b = Store(session="alpha"), Store(session="beta")
        self.assertNotEqual(a.path("w1"), b.path("w1"))
        os.environ["HERDR_SESSION"] = "alpha"
        self.assertEqual(Store().session, "alpha")


if __name__ == "__main__":
    unittest.main()
