"""Fleet: one orchestrator pane launches several teams, each in its own workspace, and gets their leads' reports."""
import os

import test_cli
from helpers import FakeEnv
from roles import mail, team as teammod


class FleetTests(FakeEnv):
    run_cli = test_cli.CliTests.run_cli
    hook = test_cli.CliTests.hook
    orch = "w1:p1"

    def up(self, *argv):
        code, out, err = self.run_cli("fleet-up", *argv, "--pane", self.orch)
        self.assertEqual(code, 0, err)
        return out

    def event(self, pane, status):
        self.set_status(pane, status)
        return self.run_cli("dispatch", env=self.hook("pane.agent_status_changed", pane, status, ws=pane.split(":")[0]))

    def fleet(self):
        return self.store.load("w1")["fleet"]

    def delegate(self, lead, role="reviewer"):
        """The lead hands part of the request to a member (which keeps the team busy until it answers)."""
        code, _, err = self.run_cli("send", "--role", role, "--text", "part of it", "--pane", lead)
        self.assertEqual(code, 0, err)
        return self.pane_of(role, self.store.load(lead.split(":")[0]))

    def launch_two(self):
        self.up("auth", "billing", "--task", "build login", "--task", "fix invoices")
        return self.fleet()["auth"]["lead"], self.fleet()["billing"]["lead"]

    # ---- launching
    def test_each_team_gets_its_own_workspace_lead_and_members(self):
        out = self.up("auth", "billing", "--task", "build login", "--task", "fix invoices")
        self.assertIn("launched 'auth'", out)
        fleet = self.fleet()
        self.assertEqual(set(fleet), {"auth", "billing"})
        self.assertNotEqual(fleet["auth"]["workspace"], fleet["billing"]["workspace"])
        for name, task in (("auth", "build login"), ("billing", "fix invoices")):
            ws, lead = fleet[name]["workspace"], fleet[name]["lead"]
            self.assertEqual(self.fake()["panes"][lead]["agent"], "claude")       # the lead's agent was started
            self.assertEqual(sum(1 for p in self.fake()["panes"].values() if p["workspace_id"] == ws), 5)
            roster = self.prompts(lead)
            self.assertEqual(len(roster), 1)                                     # roster, brief and task in one
            self.assertIn(f"팀 '{name}'", roster[0])
            self.assertIn(f"{mail.ORCHESTRATOR}의 요청", roster[0])
            self.assertTrue(roster[0].endswith(task))
            data = self.store.load(ws)
            self.assertEqual(data["parent"], {"workspace": "w1", "pane": self.orch, "name": name})
            self.assertTrue(data["asks"][lead]["from"][0]["sticky"])
        self.assertEqual(self.prompts(self.orch), [])                              # the orchestrator is not a lead
        self.assertEqual(self.store.load("w1")["panes"], {})

    def test_tasks_must_match_the_teams(self):
        code, _, err = self.run_cli("fleet-up", "a", "b", "--task", "x", "--pane", self.orch)
        self.assertEqual(code, 1)
        self.assertIn("팀마다 하나씩", err)
        self.assertEqual(self.fleet(), {})

    def test_a_team_config_can_be_picked_per_name(self):
        code, _, err = self.run_cli("fleet-up", "x=nope", "--pane", self.orch)
        self.assertEqual(code, 1)
        self.assertIn("config not found", err)
        self.assertEqual(self.fleet(), {})
        self.assertEqual(self.calls("workspace create"), [])                      # nothing opened for a bad config

    def test_worktree_mode_gives_each_team_a_branch(self):
        self.up("auth", "--worktree")
        call = self.calls("worktree create")[0]
        self.assertIn("roles/auth", call)
        self.assertEqual(self.fleet()["auth"]["path"], "/worktrees/auth/roles/auth")

    def test_the_team_works_in_the_shells_folder_not_the_orchestrator_panes(self):
        here = os.path.realpath(os.path.join(self.tmp, "other-repo"))
        os.makedirs(here)
        old = os.getcwd()
        os.chdir(here)
        self.addCleanup(os.chdir, old)
        self.up("auth")                                   # the orchestrator pane was opened in self.cwd
        call = self.calls("workspace create")[0]
        self.assertEqual(call[call.index("--cwd") + 1], here)

    def test_agents_wait_for_a_new_shell_to_reach_its_prompt(self):
        self.edit_fake(lambda s: s.update(slow_shells=2))
        self._wait(SHELL_POLL_S=0)
        self.up("auth", "--task", "build login")
        lead, ws = self.fleet()["auth"]["lead"], self.fleet()["auth"]["workspace"]
        states = {p["role"]: p["status"] for p in self.store.load(ws)["panes"].values()}
        self.assertEqual(set(states.values()), {"ready"}, states)            # lead and members all started
        self.assertEqual(self.fake()["panes"][lead]["agent"], "claude")
        self.assertEqual(len(self.prompts(lead)), 1)

    def test_a_shell_that_never_gets_ready_still_fails(self):
        self.edit_fake(lambda s: s.update(slow_shells=99))
        self._wait(SHELL_WAIT_S=0, SHELL_POLL_S=0)
        code, out, err = self.run_cli("fleet-up", "auth", "--pane", self.orch)
        lead = self.fleet()["auth"]["lead"]
        self.assertEqual(self.store.load(self.fleet()["auth"]["workspace"])["panes"][lead]["status"], "failed")
        self.assertIn("not an available shell", out + err)

    def _wait(self, **values):
        for k, v in values.items():
            old = getattr(teammod, k)
            setattr(teammod, k, v)
            self.addCleanup(setattr, teammod, k, old)

    def test_launching_again_reconciles_and_sends_a_new_request(self):
        lead, _ = self.launch_two()
        self.event(lead, "working")
        self.set_status(lead, "idle")
        out = self.up("auth", "--task", "also add logout")
        self.assertIn("reconciled 'auth'", out)
        self.assertEqual(len(self.calls("workspace create")), 2)
        self.assertTrue(self.prompts(lead)[-1].endswith("also add logout"))

    # ---- reporting back
    def test_a_lead_that_delegates_and_goes_idle_does_not_report(self):
        lead, _ = self.launch_two()
        self.event(lead, "working")
        rev = self.delegate(lead)
        self.set_output(lead, "sent tasks to the reviewer")
        self.event(lead, "idle")
        self.assertEqual(self.prompts(self.orch), [])
        ws = self.fleet()["auth"]["workspace"]
        self.assertIn(lead, self.store.load(ws)["asks"])
        self.event(rev, "working")                                         # the member answers the lead ...
        self.set_output(rev, "<<<HANDOFF\nreviewed\n>>>")
        self.event(rev, "idle")
        self.assertEqual(self.prompts(self.orch), [])
        self.event(lead, "working")                                        # ... and the lead wraps up, no block
        self.set_output(lead, "all done: login works")
        self.event(lead, "idle")
        self.assertIn("all done: login works", self.prompts(self.orch)[-1])  # quiet team: its last output counts

    def test_a_lead_that_works_alone_and_forgets_the_block_still_reports(self):
        lead, _ = self.launch_two()
        self.event(lead, "working")
        self.set_output(lead, "wrote docs/design/api.md")
        self.event(lead, "idle")
        self.assertIn("wrote docs/design/api.md", self.prompts(self.orch)[-1])

    def test_a_working_member_keeps_the_team_busy(self):
        lead, _ = self.launch_two()
        rev = self.pane_of("reviewer", self.store.load(self.fleet()["auth"]["workspace"]))
        self.event(lead, "working")
        self.set_status(rev, "working")                                    # busy on something the lead started
        self.event(lead, "idle")
        self.assertEqual(self.prompts(self.orch), [])

    def test_the_leads_handoff_block_is_reported_to_the_orchestrator(self):
        lead, _ = self.launch_two()
        self.event(lead, "working")
        self.delegate(lead)
        self.event(lead, "idle")                                           # delegated, no block yet
        self.event(lead, "working")                                        # still busy with the team
        self.set_output(lead, "noise <<<HANDOFF\nlogin done, 3 files\n>>>")
        self.event(lead, "idle")
        report = self.prompts(self.orch)[-1]
        self.assertIn("팀 'auth'", report)
        self.assertIn("(요청: build login)", report)
        self.assertIn("login done, 3 files", report)
        self.assertNotIn("noise", report)
        self.assertIn("read --fleet auth", report)
        self.assertNotIn(lead, self.store.load(self.fleet()["auth"]["workspace"])["asks"])
        self.assertEqual(self.fleet()["auth"]["reports"], 1)

    def test_an_old_block_on_screen_does_not_answer_a_new_request(self):
        lead, _ = self.launch_two()
        self.event(lead, "working")
        self.set_output(lead, "<<<HANDOFF\nfirst result\n>>>")
        self.event(lead, "idle")
        self.assertEqual(len(self.prompts(self.orch)), 1)
        code, out, err = self.run_cli("send", "--fleet", "auth", "--text", "now add logout", "--pane", self.orch)
        self.assertEqual(code, 0, err)
        self.assertIn("now add logout", self.prompts(lead)[-1])
        self.event(lead, "working")
        self.delegate(lead)
        self.event(lead, "idle")                                           # the first block is still on screen
        self.assertEqual(len(self.prompts(self.orch)), 1)
        self.set_output(lead, "<<<HANDOFF\nfirst result\n>>> ... <<<HANDOFF\nlogout done\n>>>")
        self.event(lead, "working")
        self.event(lead, "idle")
        self.assertEqual(len(self.prompts(self.orch)), 2)
        self.assertIn("logout done", self.prompts(self.orch)[-1])

    def test_a_busy_orchestrator_gets_the_report_on_its_next_idle(self):
        lead, _ = self.launch_two()
        self.set_status(self.orch, "working")
        self.event(lead, "working")
        self.set_output(lead, "<<<HANDOFF\ndone\n>>>")
        self.event(lead, "idle")
        self.assertEqual(self.prompts(self.orch), [])
        self.assertEqual(len(self.store.load("w1")["outbox"][self.orch]), 1)
        self.event(self.orch, "idle")                                      # the orchestrator's own hook flushes it
        self.assertIn("done", self.prompts(self.orch)[-1])
        self.assertEqual(self.store.load("w1")["outbox"], {})

    def test_a_request_to_a_busy_lead_waits_and_the_current_answer_still_comes(self):
        lead, _ = self.launch_two()
        self.event(lead, "working")
        before = len(self.prompts(lead))
        self.run_cli("send", "--fleet", "auth", "--text", "second", "--pane", self.orch)
        self.assertEqual(len(self.prompts(lead)), before)                  # not typed into the running turn
        self.set_output(lead, "<<<HANDOFF\nfirst answer\n>>>")
        self.event(lead, "idle")
        self.assertIn("(요청: build login)", self.prompts(self.orch)[-1])  # the first request is answered
        self.assertTrue(self.prompts(lead)[-1].endswith("second"))          # then the held one goes out
        self.event(lead, "working")
        self.delegate(lead)
        self.event(lead, "idle")                                           # same block: not the answer to "second"
        self.assertEqual(len(self.prompts(self.orch)), 1)
        self.set_output(lead, "<<<HANDOFF\nsecond answer\n>>>")
        self.event(lead, "working")
        self.event(lead, "idle")
        self.assertIn("(요청: second)", self.prompts(self.orch)[-1])

    def test_members_still_report_to_their_own_lead(self):
        lead, _ = self.launch_two()
        ws = self.fleet()["auth"]["workspace"]
        rev = self.pane_of("reviewer", self.store.load(ws))
        code, _, err = self.run_cli("send", "--role", "reviewer", "--text", "review", "--pane", lead)
        self.assertEqual(code, 0, err)
        self.event(rev, "working")
        self.set_output(rev, "<<<HANDOFF\nlgtm\n>>>")
        self.event(rev, "idle")
        self.assertIn("lgtm", self.prompts(lead)[-1])
        self.assertEqual(self.prompts(self.orch), [])

    def test_an_orchestrator_that_leads_its_own_team(self):
        code, _, err = self.run_cli("team-up", "--team", "dev", "--pane", self.orch)
        self.assertEqual(code, 0, err)
        own = len(self.store.load("w1")["panes"])
        lead, _ = self.launch_two()
        self.assertEqual(len(self.store.load("w1")["panes"]), own)        # its own team is untouched
        self.event(lead, "working")
        self.set_output(lead, "<<<HANDOFF\nauth done\n>>>")
        self.event(lead, "idle")
        self.assertIn("auth done", self.prompts(self.orch)[-1])
        self.run_cli("team-down", "--pane", self.orch)
        self.assertEqual(set(self.fleet()), {"auth", "billing"})          # dismantling its own team keeps the fleet

    # ---- looking and closing
    def test_status_and_read(self):
        lead, _ = self.launch_two()
        code, out, _ = self.run_cli("fleet", "--pane", self.orch)
        self.assertEqual(code, 0)
        self.assertIn("auth", out)
        self.assertIn("working", out)
        self.assertIn("task: build login", out)
        self.set_output(lead, "<<<HANDOFF\nhello\n>>>")
        _, out, _ = self.run_cli("read", "--fleet", "auth", "--marker", "--pane", self.orch)
        self.assertEqual(out.strip(), "hello")
        code, _, err = self.run_cli("read", "--fleet", "nope", "--pane", self.orch)
        self.assertEqual(code, 1)
        self.assertIn("auth, billing", err)

    def test_send_needs_exactly_one_target(self):
        code, _, err = self.run_cli("send", "--text", "x", "--pane", self.orch)
        self.assertEqual(code, 1)
        self.assertIn("하나만", err)

    def test_fleet_down_closes_workspaces_and_forgets_them(self):
        self.launch_two()
        wss = [e["workspace"] for e in self.fleet().values()]
        code, out, _ = self.run_cli("fleet-down", "auth", "--pane", self.orch)
        self.assertEqual(code, 0)
        self.assertEqual(set(self.fleet()), {"billing"})
        self.assertEqual(self.fake()["closed_workspaces"], [wss[0]])
        self.assertFalse(os.path.exists(self.store.path(wss[0])))
        self.run_cli("fleet-down", "--pane", self.orch)
        self.assertEqual(self.fleet(), {})
        self.assertEqual(sorted(self.fake()["closed_workspaces"]), sorted(wss))
        self.assertFalse(any(p["workspace_id"] in wss for p in self.fake()["panes"].values()))

    def test_a_long_report_keeps_its_beginning(self):
        lead, _ = self.launch_two()
        self.event(lead, "working")
        body = "HEAD: ui.md 고칠 곳\n" + "x" * 5000 + "\nTAIL"
        self.set_output(lead, f"<<<HANDOFF\n{body}\n>>>")
        self.event(lead, "idle")
        report = self.prompts(self.orch)[-1]
        self.assertIn("HEAD: ui.md 고칠 곳", report)
        self.assertNotIn("TAIL", report)
        self.assertIn("이하 생략", report)
        self.assertLess(len(report), mail.REPORT_CHARS + 500)

    def test_a_quiet_team_without_a_new_block_reports_the_screens_end_not_the_old_block(self):
        lead, _ = self.launch_two()
        self.event(lead, "working")
        self.set_output(lead, "<<<HANDOFF\nfirst result\n>>>")
        self.event(lead, "idle")
        self.run_cli("send", "--fleet", "auth", "--text", "second", "--pane", self.orch)
        self.event(lead, "working")
        self.set_output(lead, "<<<HANDOFF\nfirst result\n>>>\n" + "y" * 5000 + "\nsecond done")
        self.event(lead, "idle")
        report = self.prompts(self.orch)[-1]
        self.assertIn("second done", report)
        self.assertNotIn("first result", report)

    def test_the_echoed_instruction_is_not_an_answer(self):
        lead, _ = self.launch_two()
        rev = self.delegate(lead)
        self.event(lead, "working")
        # the lead's screen shows our request, whose instruction contains "<<<HANDOFF ... >>>"
        self.set_output(lead, mail.orchestrator_request("finish it") + "\nwaiting for the reviewer")
        self.event(lead, "idle")
        self.assertEqual(self.prompts(self.orch), [])
        self.event(rev, "working")
        self.set_output(rev, "<<<HANDOFF\nok\n>>>")
        self.event(rev, "idle")
        self.event(lead, "working")
        self.set_output(lead, mail.orchestrator_request("finish it") + "\n<<<HANDOFF\nreal summary\n>>>")
        self.event(lead, "idle")
        self.assertIn("real summary", self.prompts(self.orch)[-1])

    def test_a_lead_reports_with_the_command(self):
        lead, _ = self.launch_two()
        self.assertIn(f"{mail.CLI} report <<'EOF'", self.prompts(lead)[0])
        rev = self.delegate(lead)
        self.event(lead, "working")
        code, out, err = self.run_cli("report", "--text", "login done\n" + "q" * 5000, "--pane", lead)
        self.assertEqual(code, 0, err)
        self.assertIn(mail.ORCHESTRATOR, out)
        msg = self.prompts(self.orch)[-1]
        self.assertIn("팀 'auth'", msg)
        self.assertIn("login done", msg)
        self.assertIn("전체 보고: ", msg)
        self.assertEqual(self.fleet()["auth"]["reports"], 1)
        self.event(lead, "idle")                                           # no second report from the screen
        self.assertEqual(len(self.prompts(self.orch)), 1)

    def test_an_unasked_lead_report_goes_to_the_orchestrator(self):
        self.up("auth")                                                    # launched without a task
        lead = self.fleet()["auth"]["lead"]
        code, _, err = self.run_cli("report", "--text", "question: which DB?", "--pane", lead)
        self.assertEqual(code, 0, err)
        self.assertIn("question: which DB?", self.prompts(self.orch)[-1])

    def test_a_request_wrapped_in_a_handoff_block_does_not_answer_the_orchestrator(self):
        lead, _ = self.launch_two()
        self.event(lead, "working")
        code, _, err = self.run_cli("send", "--role", "reviewer", "--text", "<<<HANDOFF\nreview d9..33\n>>>",
                                    "--pane", lead)
        self.assertEqual(code, 0, err)
        ws = self.fleet()["auth"]["workspace"]
        rev = self.pane_of("reviewer", self.store.load(ws))
        self.assertNotIn("<<<HANDOFF\nreview", self.prompts(rev)[-1])         # delivered without a live opener
        self.assertEqual(self.store.load(ws)["asks"][rev]["from"][0]["summary"], "review d9..33")
        self.set_status(rev, "working")
        # the lead's screen echoes its own `send --text "<<<HANDOFF ..."` while the reviewer works
        self.set_output(lead, 'roles send --role reviewer --text "<<<HANDOFF\nreview d9..33\n>>>"\nwaiting')
        self.event(lead, "idle")
        self.assertEqual(self.prompts(self.orch), [])
        code, _, err = self.run_cli("report", "--text", "APPROVED\nnotes", "--pane", rev)
        self.assertEqual(code, 0, err)
        header = self.prompts(lead)[-1]
        self.assertIn("(요청: review d9..33)", header)
        self.assertNotIn("<<<HANDOFF", header)
        self.set_status(rev, "idle")
        self.set_status(self.pane_of("tester", self.store.load(ws)), "working")    # E2E still running
        self.event(lead, "working")                                        # the lead's screen: the report, then more
        self.set_output(lead, header + "\nforwarding to implementer >>>")
        self.event(lead, "idle")
        self.assertEqual(self.prompts(self.orch), [])                     # the member's report is not the lead's

    def test_a_block_written_while_members_work_waits_for_the_team(self):
        lead, _ = self.launch_two()
        rev = self.delegate(lead)
        self.event(lead, "working")
        self.set_status(rev, "working")
        self.set_output(lead, "<<<HANDOFF\nall done\n>>>")
        self.event(lead, "idle")
        self.assertEqual(self.prompts(self.orch), [])
        self.set_status(rev, "idle")
        self.event(lead, "working")
        self.event(lead, "idle")
        self.assertIn("all done", self.prompts(self.orch)[-1])
