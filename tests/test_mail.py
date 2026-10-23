"""Reporting and member-to-member requests: `send` tracks who asked, and the answer goes back when the work is done."""
import test_cli
from helpers import FakeEnv
from roles import mail


class MailTests(FakeEnv):
    run_cli = test_cli.CliTests.run_cli
    hook = test_cli.CliTests.hook

    def setUp(self):
        super().setUp()
        self.run_cli("team-up", "--team", "dev", "--pane", "w1:p1")
        self.lead = "w1:p1"
        self.impl = self.pane_of("implementer")
        self.rev = self.pane_of("reviewer")

    def event(self, pane, status):
        self.set_status(pane, status)
        return self.run_cli("dispatch", env=self.hook("pane.agent_status_changed", pane, status))

    def send(self, role, text, sender):
        code, out, err = self.run_cli("send", "--role", role, "--text", text, "--pane", sender)
        self.assertEqual(code, 0, err)
        return out

    def finish(self, pane, output):
        self.event(pane, "working")
        self.set_output(pane, output)
        self.event(pane, "idle")

    # ---- briefing
    def test_members_are_told_the_team_and_how_to_report(self):
        brief = self.prompts(self.rev)[0]
        self.assertIn("리드는 'Planner'(planner)", brief)
        self.assertIn("- implementer:", brief)
        self.assertNotIn("- reviewer:", brief)                  # not itself
        self.assertIn(f"{mail.CLI} report <<'EOF'", brief)
        self.assertIn(f"{mail.CLI} send --role <역할>", brief)
        self.assertIn("자동으로 보고됩니다", self.prompts(self.lead)[-1])     # the lead's roster says so too

    # ---- reporting back
    def test_finished_work_is_reported_to_the_lead(self):
        out = self.send("reviewer", "check the login flow", self.lead)
        self.assertIn("보고됩니다", out)
        self.assertIn("Planner(planner, 리드)의 요청", self.prompts(self.rev)[-1])
        self.finish(self.rev, "noise <<<HANDOFF\nLGTM, one nit\n>>> tail")
        report = self.prompts(self.lead)[-1]
        self.assertIn("Reviewer(reviewer) 작업 완료 보고 (요청: check the login flow)", report)
        self.assertIn("LGTM, one nit", report)
        self.assertNotIn("noise", report)
        self.assertEqual(self.store.load("w1")["asks"], {})

    def test_the_idle_right_after_the_prompt_is_not_an_answer(self):
        self.send("reviewer", "check", self.lead)
        before = len(self.prompts(self.lead))
        self.event(self.rev, "idle")                     # baseline idle, inside the grace period, never worked
        self.assertEqual(len(self.prompts(self.lead)), before)
        self.assertIn(self.rev, self.store.load("w1")["asks"])

    def test_a_busy_lead_gets_the_report_when_it_is_idle(self):
        self.send("reviewer", "check", self.lead)
        self.set_status(self.lead, "working")
        before = len(self.prompts(self.lead))
        self.finish(self.rev, "<<<HANDOFF\nok\n>>>")
        self.assertEqual(len(self.prompts(self.lead)), before)          # not interrupted mid-task
        self.assertEqual(len(self.store.load("w1")["outbox"][self.lead]), 1)
        self.event(self.lead, "idle")
        self.assertIn("작업 완료 보고", self.prompts(self.lead)[-1])
        self.assertEqual(self.store.load("w1")["outbox"], {})

    def test_a_busy_target_gets_the_request_after_its_current_task(self):
        self.set_status(self.rev, "working")                 # busy with something else
        self.set_output(self.rev, "<<<HANDOFF\nold task\n>>>")
        before = len(self.prompts(self.rev))
        out = self.send("reviewer", "check", self.lead)
        self.assertIn("queued", out)
        self.assertEqual(len(self.prompts(self.rev)), before)          # not typed into its running turn
        lead_before = len(self.prompts(self.lead))
        self.event(self.rev, "idle")                                    # the old task ends: deliver, don't report
        self.assertTrue(self.prompts(self.rev)[-1].endswith("\n\ncheck"))
        self.assertEqual(len(self.prompts(self.lead)), lead_before)
        self.finish(self.rev, "<<<HANDOFF\nnew answer\n>>>")
        report = self.prompts(self.lead)[-1]
        self.assertIn("new answer", report)
        self.assertNotIn("old task", report)

    def test_a_request_queued_behind_another_is_reported_separately(self):
        self.send("reviewer", "first", self.lead)
        self.event(self.rev, "working")
        self.edit_fake(lambda s: s["panes"][self.rev].update(agent_status="working"))
        self.send("reviewer", "second", self.impl)                     # reviewer is busy with "first"
        self.set_output(self.rev, "<<<HANDOFF\nanswer one\n>>>")
        self.event(self.rev, "idle")
        self.assertIn("answer one", self.prompts(self.lead)[-1])
        self.assertTrue(self.prompts(self.rev)[-1].endswith("\n\nsecond"))
        impl_before = len(self.prompts(self.impl))
        self.finish(self.rev, "<<<HANDOFF\nanswer two\n>>>")
        self.assertEqual(len(self.prompts(self.impl)), impl_before + 1)
        self.assertIn("answer two", self.prompts(self.impl)[-1])

    def test_long_output_is_cut_and_points_to_read(self):
        self.send("reviewer", "check", self.lead)
        self.finish(self.rev, "x" * 10_000)
        report = self.prompts(self.lead)[-1]
        self.assertLess(len(report), mail.REPORT_CHARS + 500)
        self.assertIn("read --role reviewer", report)

    def test_a_member_without_a_block_is_not_reported_as_the_instructions_placeholder(self):
        self.send("reviewer", "check", self.lead)
        request = self.prompts(self.rev)[-1]                  # shown on its screen, with "<<<HANDOFF ... >>>"
        self.finish(self.rev, request + "\nchecked: no issues")
        report = self.prompts(self.lead)[-1]
        self.assertIn("checked: no issues", report)

    def test_a_long_handoff_block_keeps_its_beginning(self):
        self.send("reviewer", "check", self.lead)
        self.finish(self.rev, "<<<HANDOFF\nVERDICT: 2 bugs\n" + "z" * 6000 + "\nEND\n>>>")
        report = self.prompts(self.lead)[-1]
        self.assertIn("VERDICT: 2 bugs", report)
        self.assertNotIn("END", report)

    # ---- explicit `roles report`
    def report(self, pane, text):
        code, out, err = self.run_cli("report", "--text", text, "--pane", pane)
        self.assertEqual(code, 0, err)
        return out

    def test_report_answers_the_request_and_keeps_the_whole_text_in_a_file(self):
        self.send("reviewer", "check", self.lead)
        self.event(self.rev, "working")
        body = "VERDICT: 2 bugs\n" + "z" * 6000 + "\nEND"
        out = self.report(self.rev, body)
        self.assertIn("Planner(planner, 리드)", out)
        path = out.split("전문: ")[1].strip()
        with open(path) as f:
            self.assertEqual(f.read(), body + "\n")
        msg = self.prompts(self.lead)[-1]
        self.assertIn("VERDICT: 2 bugs", msg)
        self.assertNotIn("END", msg)
        self.assertIn(path, msg)
        self.assertEqual(self.store.load("w1")["asks"], {})
        before = len(self.prompts(self.lead))
        self.set_output(self.rev, "<<<HANDOFF\nsomething else\n>>>")
        self.event(self.rev, "idle")                       # the turn ends after the report: no second report
        self.assertEqual(len(self.prompts(self.lead)), before)

    def test_an_unasked_report_goes_to_the_lead(self):
        self.report(self.impl, "refactored auth.py")
        self.assertIn("refactored auth.py", self.prompts(self.lead)[-1])
        self.assertIn("요청 없이 보고", self.prompts(self.lead)[-1])

    def test_an_empty_report_is_refused(self):
        code, _, err = self.run_cli("report", "--text", "  ", "--pane", self.rev)
        self.assertEqual(code, 1)
        self.assertIn("비어 있습니다", err)

    # ---- members talking to each other
    def test_member_to_member_request_is_answered_and_copied_to_the_lead(self):
        self.send("reviewer", "review my diff", self.impl)
        self.assertIn("Implementer(implementer)의 요청", self.prompts(self.rev)[-1])
        self.assertIn("참조: Implementer(implementer) → Reviewer(reviewer): review my diff",
                      self.prompts(self.lead)[-1])
        self.finish(self.rev, "<<<HANDOFF\nrename x\n>>>")
        self.assertIn("rename x", self.prompts(self.impl)[-1])
        self.assertIn("의 요청을 마쳤습니다: rename x", self.prompts(self.lead)[-1])

    def test_every_asker_gets_the_answer(self):
        self.send("reviewer", "from lead", self.lead)
        self.send("reviewer", "from impl", self.impl)
        self.finish(self.rev, "<<<HANDOFF\ndone\n>>>")
        self.assertIn("(요청: from lead)", self.prompts(self.lead)[-1])
        self.assertIn("(요청: from impl)", self.prompts(self.impl)[-1])

    # ---- what is not tracked
    def test_a_send_from_outside_the_team_is_delivered_as_is(self):
        self.edit_fake(lambda s: s["panes"].__setitem__("w1:p9", self._pane("w1:p9", agent="claude", status="idle")))
        out = self.send("reviewer", "plain", "w1:p9")
        self.assertEqual(self.prompts(self.rev)[-1], "plain")
        self.assertNotIn("보고됩니다", out)
        self.assertEqual(self.store.load("w1")["asks"], {})

    def test_a_running_workflow_step_is_left_to_the_workflow(self):
        self.run_cli("run", "--workflow", "feature", "--input", "go", "--pane", self.lead)
        self.send("planner", "also this", self.impl)        # planner is the workflow's current step
        self.assertEqual(self.store.load("w1")["asks"], {})

    def test_closing_a_pane_drops_its_asks(self):
        self.send("reviewer", "check", self.impl)
        self.run_cli("dispatch", env=self.hook("pane.closed", self.impl))
        self.assertEqual(self.store.load("w1")["asks"][self.rev]["from"], [])
        self.run_cli("dispatch", env=self.hook("pane.closed", self.rev))
        self.assertEqual(self.store.load("w1")["asks"], {})

    def test_team_down_clears_messages(self):
        self.send("reviewer", "check", self.lead)
        self.run_cli("team-down", "--pane", self.lead)
        data = self.store.load("w1")
        self.assertEqual((data["asks"], data["outbox"]), ({}, {}))
