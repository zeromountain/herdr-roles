import contextlib
import io
import json
import os
import unittest

from helpers import FakeEnv
from roles import cli, config


class CliTests(FakeEnv):
    def run_cli(self, *argv, env=None):
        out, err = io.StringIO(), io.StringIO()
        old = {k: os.environ.get(k) for k in (env or {})}
        os.environ.update(env or {})
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = cli.main(list(argv), h=self.h)
        finally:
            for k, v in old.items():
                os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        return code, out.getvalue(), err.getvalue()

    def team_up(self):
        return self.run_cli("team-up", "--team", "dev", "--pane", "w1:p1")

    def test_init_copies_examples_without_clobbering(self):
        fresh = os.path.join(self.tmp, "fresh-config")
        os.environ["ROLES_CONFIG_DIR"] = fresh
        code, out, _ = self.run_cli("init")
        self.assertEqual(code, 0)
        self.assertTrue(os.path.exists(os.path.join(fresh, "teams", "dev.toml")))
        with open(os.path.join(fresh, "roles.toml"), "a") as f:
            f.write("# mine\n")
        self.run_cli("init")
        with open(os.path.join(fresh, "roles.toml")) as f:
            self.assertIn("# mine", f.read())

    def test_team_up_dry_run_then_real(self):
        code, out, _ = self.run_cli("team-up", "--team", "dev", "--pane", "w1:p1", "--dry-run")
        self.assertEqual(code, 0)
        self.assertIn("[dry-run]", out)
        self.assertEqual(len(self.fake()["panes"]), 1)
        code, out, _ = self.team_up()
        self.assertEqual(code, 0)
        self.assertEqual(len(self.fake()["panes"]), 5)

    def test_team_defaults_to_the_only_team_or_setting(self):
        code, _, _ = self.run_cli("team-up", "--pane", "w1:p1")      # default_team = dev in config.toml
        self.assertEqual(code, 0)

    def test_errors_exit_nonzero_with_a_message(self):
        code, _, err = self.run_cli("team-up", "--team", "nope", "--pane", "w1:p1")
        self.assertEqual(code, 1)
        self.assertIn("config not found", err)
        code, _, err = self.run_cli("team-up", "--team", "dev", "--pane", "w1:p99")
        self.assertEqual(code, 1)

    def test_pane_defaults_to_the_calling_shell_not_the_focused_one(self):
        code, _, _ = self.run_cli("team-up", "--team", "dev", env={"HERDR_PANE_ID": "w1:p1"})
        self.assertEqual(code, 0)
        self.assertEqual(len(self.fake()["panes"]), 5)

    def test_send_and_read_by_role(self):
        self.team_up()
        rev = self.pane_of("reviewer")
        code, out, _ = self.run_cli("send", "--role", "reviewer", "--text", "check this", "--pane", "w1:p1")
        self.assertEqual(code, 0)
        self.assertEqual(self.prompts(rev)[-1], "check this")
        self.set_output(rev, "noise <<<HANDOFF\nLGTM\n>>> tail")
        _, out, _ = self.run_cli("read", "--role", "reviewer", "--marker", "--pane", "w1:p1")
        self.assertEqual(out.strip(), "LGTM")
        code, _, err = self.run_cli("send", "--role", "wizard", "--text", "x", "--pane", "w1:p1")
        self.assertEqual(code, 1)
        self.assertIn("알 수 없는 역할", err)

    def test_send_to_shell_role_runs_the_command(self):
        self.team_up()
        runner = self.pane_of("runner")
        self.run_cli("send", "--role", "runner", "--text", "echo hi", "--pane", "w1:p1")
        self.assertEqual(self.fake()["panes"][runner]["ran"], ["echo hi"])

    def test_status_json(self):
        self.team_up()
        code, out, _ = self.run_cli("status", "--json", "--pane", "w1:p1")
        data = json.loads(out)
        self.assertEqual(data["team"], "dev")
        self.assertEqual(len(data["rows"]), 5)
        self.assertTrue(all(r["state"] == "ready" for r in data["rows"]))

    def test_reapply_restores_metadata_after_a_server_restart(self):
        self.team_up()
        victim = self.pane_of("tester")
        self.edit_fake(lambda s: [p.update(meta={}) for p in s["panes"].values()])       # herdr forgot it all
        self.edit_fake(lambda s: s["panes"].pop(victim))                                   # and one pane is gone
        code, _, _ = self.run_cli("reapply", "--quiet")
        self.assertEqual(code, 0)
        panes = self.fake()["panes"]
        self.assertTrue(all(p["meta"].get("title") for p in panes.values()))
        self.assertNotIn(victim, self.store.load("w1")["panes"])

    def test_reapply_is_a_noop_without_state(self):
        os.rename(self.store.root, self.store.root + ".gone") if os.path.isdir(self.store.root) else None
        code, out, _ = self.run_cli("reapply")
        self.assertEqual((code, out), (0, ""))

    def hook(self, event, pane, status=None, ws="w1"):
        payload = {"event": event.replace(".", "_"), "data": {"type": event.replace(".", "_"), "pane_id": pane,
                                                              "workspace_id": ws}}
        if status:
            payload["data"]["agent_status"] = status
        return {"HERDR_PLUGIN_EVENT": event, "HERDR_PLUGIN_EVENT_JSON": json.dumps(payload)}

    def test_dispatch_drives_a_workflow_from_hook_env(self):
        self.team_up()
        self.run_cli("run", "--workflow", "feature", "--input", "go", "--pane", "w1:p1")
        self.set_status("w1:p1", "working")
        self.run_cli("dispatch", env=self.hook("pane.agent_status_changed", "w1:p1", "working"))
        self.set_output("w1:p1", "the plan")
        self.set_status("w1:p1", "done")
        code, out, _ = self.run_cli("dispatch", env=self.hook("pane.agent_status_changed", "w1:p1", "done"))
        self.assertEqual(code, 0)
        self.assertIn("pending:plan->build", out)
        code, out, _ = self.run_cli("advance", "--pane", "w1:p1")
        self.assertIn("build", out)
        self.assertIn("the plan", self.prompts(self.pane_of("implementer"))[-1])

    def test_dispatch_ignores_untracked_workspaces_without_touching_config(self):
        os.environ["ROLES_CONFIG_DIR"] = os.path.join(self.tmp, "does-not-exist")
        code, out, err = self.run_cli("dispatch", env=self.hook("pane.agent_status_changed", "w9:p1", "done", ws="w9"))
        self.assertEqual((code, out, err), (0, "", ""))

    def test_dispatch_pane_closed_removes_the_mapping(self):
        self.team_up()
        victim = self.pane_of("tester")
        self.run_cli("dispatch", env=self.hook("pane.closed", victim))
        self.assertNotIn(victim, self.store.load("w1")["panes"])

    def test_run_uses_the_team_default_workflow(self):
        self.team_up()
        os.remove(os.path.join(self.cfg, "config.toml"))
        code, out, _ = self.run_cli("run", "--input", "hi", "--pane", "w1:p1")
        self.assertEqual(code, 0)
        self.assertIn("workflow 'feature' started", out)

    def test_run_stdin_and_stop(self):
        self.team_up()
        self.run_cli("run", "--workflow", "feature", "--input", "x", "--pane", "w1:p1")
        code, out, _ = self.run_cli("stop", "--pane", "w1:p1")
        self.assertIn("stopped", out)
        _, out, _ = self.run_cli("status", "--pane", "w1:p1")
        self.assertIn("stopped", out)

    def test_team_down_via_cli(self):
        self.team_up()
        code, out, _ = self.run_cli("team-down", "--pane", "w1:p1")
        self.assertEqual(code, 0)
        self.assertEqual(sorted(self.fake()["panes"]), ["w1:p1"])

    def test_picker_menu_assigns_a_role_and_builds_a_team(self):
        from unittest import mock
        env = {"HERDR_PLUGIN_CONTEXT_JSON": json.dumps({"focused_pane_id": "w1:p1"})}
        # menu: 1=assign -> role #3 (sorted: implementer, planner, reviewer, ...) ; 2=team-up -> team #1 ; q
        answers = iter(["1", "3", "2", "1", "q"])
        with mock.patch("builtins.input", lambda *_: next(answers)):
            code, out, err = self.run_cli("pick", env=env)
        self.assertEqual(code, 0, err)
        data = self.store.load("w1")
        self.assertEqual(data["team"], "dev")
        self.assertEqual(len(self.fake()["panes"]), 5)
        self.assertEqual(data["panes"]["w1:p1"]["role"], "planner")        # team-up made the pane lead of its role

    def test_picker_survives_bad_input(self):
        from unittest import mock
        answers = iter(["9", "x", "q"])
        with mock.patch("builtins.input", lambda *_: next(answers)):
            code, out, _ = self.run_cli("pick", env={"HERDR_PLUGIN_CONTEXT_JSON": json.dumps({"focused_pane_id": "w1:p1"})})
        self.assertEqual(code, 0)
        self.assertIn("잘못된 입력", out)

    def test_board_once(self):
        self.team_up()
        code, out, _ = self.run_cli("board", "--once", "--pane", "w1:p1")
        self.assertEqual(code, 0)
        self.assertIn("team: dev", out)
        self.assertIn("reviewer", out)


if __name__ == "__main__":
    unittest.main()
