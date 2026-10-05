import os
import unittest

from helpers import FakeEnv
from roles import config, team as teammod, workflow as wf_mod
from roles.state import now_ms


class WorkflowTests(FakeEnv):
    def setUp(self):
        super().setUp()
        teammod.team_up(self.h, self.store, self.roles, self.team, "w1:p1", self.settings)
        self.plan_pane = "w1:p1"
        self.impl = self.pane_of("implementer")
        self.rev = self.pane_of("reviewer")
        self.wf = config.load_workflow("feature", self.roles)

    def start(self, text="로그인 만들기"):
        return wf_mod.start(self.h, self.store, self.roles, self.wf, "w1", text)

    def event(self, pane, status):
        self.set_status(pane, status)
        return wf_mod.on_status(self.h, self.store, self.roles, "w1", pane, status, self.settings)

    def finish(self, pane, output):
        """Agent goes working -> done with the given terminal output."""
        self.event(pane, "working")
        self.set_output(pane, output)
        return self.event(pane, "done")

    def run_state(self):
        return self.store.load("w1")["run"]

    def test_start_prompts_the_entry_role(self):
        run = self.start()
        self.assertEqual(run["current"]["pane"], self.plan_pane)
        self.assertIn("로그인 만들기", self.prompts(self.plan_pane)[-1])

    def test_manual_step_waits_then_advance_delivers_the_plan(self):
        self.start()
        res = self.finish(self.plan_pane, "noise\n<<<HANDOFF\n1) users table\n2) login route\n>>>\n$")
        self.assertEqual(res, "pending:plan->build")
        self.assertEqual(self.run_state()["pending"]["step"], "build")
        self.assertTrue(any("인계 대기" in n["title"] for n in self.fake()["notifications"]))
        before = len(self.prompts(self.impl))
        self.assertEqual(wf_mod.advance(self.h, self.store, self.roles, "w1"), "build")
        sent = self.prompts(self.impl)[before:]
        self.assertEqual(len(sent), 1)
        self.assertIn("1) users table", sent[0])             # marker block only, not the surrounding noise
        self.assertNotIn("noise", sent[0])
        self.assertEqual(self.run_state()["current"]["pane"], self.impl)

    def test_full_loop_with_git_diff_review_and_fix(self):
        self.git_repo_with_change()
        self.start()
        self.finish(self.plan_pane, "plan")
        wf_mod.advance(self.h, self.store, self.roles, "w1")
        res = self.finish(self.impl, "implemented")
        self.assertEqual(res, "handoff:build->review")        # review is auto
        review_prompt = self.prompts(self.rev)[-1]
        self.assertIn("+two", review_prompt)                  # real git diff was handed over
        res = self.finish(self.rev, "CHANGES\n- rename x")
        self.assertEqual(res, "handoff:review->fix")
        self.assertIn("rename x", self.prompts(self.impl)[-1])
        self.assertEqual(self.run_state()["current"]["pane"], self.impl)
        n = len(self.prompts(self.rev))
        self.assertEqual(self.finish(self.impl, "fixed"), "handoff:fix->review")   # loops back to review
        self.assertEqual(len(self.prompts(self.rev)), n + 1)
        self.assertEqual(self.finish(self.rev, "APPROVED"), "done")
        self.assertEqual(self.run_state()["state"], "done")

    def test_approved_review_ends_the_run(self):
        self.git_repo_with_change()
        self.start()
        self.finish(self.plan_pane, "plan")
        wf_mod.advance(self.h, self.store, self.roles, "w1")
        self.finish(self.impl, "done")
        res = self.finish(self.rev, "APPROVED")
        self.assertEqual(res, "done")
        self.assertEqual(self.run_state()["state"], "done")
        self.assertTrue(any("완료" in n["title"] for n in self.fake()["notifications"]))

    def test_duplicate_events_do_not_trigger_a_second_handoff(self):
        self.start()
        self.finish(self.plan_pane, "plan")
        again = self.event(self.plan_pane, "done")
        self.assertIsNone(again)
        self.assertEqual(len(self.prompts(self.impl)), 1)     # only the role preamble, never a handoff

    def test_baseline_idle_right_after_sending_is_ignored(self):
        self.start()
        self.assertIsNone(self.event(self.plan_pane, "idle"))
        self.assertEqual(self.run_state()["state"], "running")
        self.assertIsNotNone(self.run_state()["current"])

    def test_idle_without_working_is_accepted_after_the_grace_period(self):
        self.start()
        self.edit_run(lambda r: r["current"].update(sent_at=now_ms() - 60_000))
        self.set_output(self.plan_pane, "fast plan")
        self.assertEqual(self.event(self.plan_pane, "idle"), "pending:plan->build")

    def edit_run(self, fn):
        data = self.store.load("w1")
        fn(data["run"])
        self.store.save("w1", data)

    def test_max_iterations_halts_the_loop(self):
        self.git_repo_with_change()
        with open(os.path.join(self.cfg, "workflows", "feature.toml")) as f:
            body = f.read().replace("max_iterations = 3", "max_iterations = 1")
        with open(os.path.join(self.cfg, "workflows", "feature.toml"), "w") as f:
            f.write(body)
        self.wf = config.load_workflow("feature", self.roles)
        self.start()
        self.finish(self.plan_pane, "plan")
        wf_mod.advance(self.h, self.store, self.roles, "w1")
        self.finish(self.impl, "impl")
        self.assertEqual(self.finish(self.rev, "CHANGES a"), "handoff:review->fix")    # 1st fix allowed
        self.assertEqual(self.finish(self.impl, "fix 1"), "handoff:fix->review")
        res = self.finish(self.rev, "CHANGES again")           # would be the 2nd fix
        self.assertEqual(res, "halted")
        run = self.run_state()
        self.assertEqual(run["state"], "halted")
        self.assertIn("max_iterations", run["reason"])

    def test_max_hops_halts(self):
        self.start()
        self.edit_run(lambda r: r.update(hops=11))
        res = self.finish(self.plan_pane, "plan")
        self.assertEqual(res, "halted")
        self.assertIn("max_hops", self.run_state()["reason"])

    def test_missing_target_role_halts_instead_of_crashing(self):
        self.start()
        data = self.store.load("w1")
        data["panes"].pop(self.impl)
        self.store.save("w1", data)
        res = self.finish(self.plan_pane, "plan")
        self.assertEqual(res, "halted")
        self.assertIn("implementer", self.run_state()["reason"])

    def test_blocked_agent_notifies_without_advancing(self):
        self.start()
        self.assertEqual(self.event(self.plan_pane, "blocked"), "blocked")
        self.assertTrue(any("입력 필요" in n["title"] for n in self.fake()["notifications"]))
        self.assertEqual(self.run_state()["state"], "running")

    def test_closing_the_working_pane_halts_the_run(self):
        self.start()
        self.edit_fake(lambda s: s["panes"].pop(self.plan_pane))
        wf_mod.on_pane_closed(self.h, self.store, "w1", self.plan_pane)
        self.assertEqual(self.run_state()["state"], "halted")
        self.assertNotIn(self.plan_pane, self.store.load("w1")["panes"])

    def test_start_refuses_a_busy_entry_pane(self):
        self.set_status(self.plan_pane, "working")
        with self.assertRaisesRegex(wf_mod.WorkflowError, "작업 중"):
            self.start()
        run = wf_mod.start(self.h, self.store, self.roles, self.wf, "w1", "now", force=True)
        self.assertEqual(run["state"], "running")

    def test_cannot_start_twice_without_force(self):
        self.start()
        with self.assertRaises(wf_mod.WorkflowError):
            self.start()
        wf_mod.start(self.h, self.store, self.roles, self.wf, "w1", "again", force=True)

    def test_events_from_unrelated_panes_are_ignored(self):
        self.start()
        self.assertIsNone(self.event(self.rev, "working"))
        self.assertFalse(self.run_state()["current"]["seen_working"])

    def test_stop(self):
        self.start()
        self.assertTrue(wf_mod.stop(self.store, "w1"))
        self.assertEqual(self.run_state()["state"], "stopped")


if __name__ == "__main__":
    unittest.main()
