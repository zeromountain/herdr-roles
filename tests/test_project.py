"""Per-project config: <repo>/.herdr-roles/ layered over the global config dir."""
import os
import subprocess
from unittest import mock

import test_cli
from helpers import FakeEnv
from roles import config, setup
from test_setup import TOSS_DEFAULTS, scripted

PROJECT_ROLES = """
[roles.designer]
label = "Designer"
agent = "claude"

[roles.reviewer]
label = "Project Reviewer"
agent = "claude"
"""

WEB_TEAM = """
[team]
name = "web"

[[member]]
role = "planner"
lead = true

[[member]]
role = "designer"

[[member]]
role = "reviewer"
"""

DESIGN_FLOW = """
[[step]]
id = "plan"
role = "planner"

[[step]]
id = "design"
role = "designer"
from = "plan"
"""


class ProjectTests(FakeEnv):
    run_cli = test_cli.CliTests.run_cli
    hook = test_cli.CliTests.hook

    def setUp(self):
        super().setUp()
        self._set_env("ROLES_PROJECT_DIR", None)
        self.proj = os.path.join(self.cwd, ".herdr-roles")
        self.write(self.proj, "roles.toml", PROJECT_ROLES)
        self.write(self.proj, "teams/web.toml", WEB_TEAM)
        self.write(self.proj, "workflows/design.toml", DESIGN_FLOW)
        self.write(self.proj, "config.toml", '[settings]\ndefault_team = "web"\n')
        self.outside = os.path.join(self.tmp, "elsewhere")
        os.makedirs(self.outside)
        old = os.getcwd()
        os.chdir(self.outside)          # the shell's own cwd is never the project: only the pane's cwd is
        self.addCleanup(os.chdir, old)

    def write(self, base, rel, text):
        path = os.path.join(base, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(text)

    def move_lead(self, cwd):
        self.edit_fake(lambda s: s["panes"]["w1:p1"].update(cwd=cwd))

    # ---- loading
    def test_project_layers_over_global(self):
        config.set_project(self.proj)
        roles = config.load_roles()
        self.assertIn("planner", roles)                                 # global
        self.assertIn("designer", roles)                                # project
        self.assertEqual(roles["reviewer"].label, "Project Reviewer")    # project wins by name
        settings = config.load_settings()
        self.assertEqual(settings["default_team"], "web")                # project wins per key
        self.assertEqual(settings["default_workflow"], "feature")        # global key kept
        self.assertEqual(config.list_names("teams"), ["dev", "web"])
        self.assertEqual(config.load_team("web", roles).lead.role, "planner")   # project team, global lead role

    def test_find_project_walks_up(self):
        deep = os.path.join(self.cwd, "src", "app")
        os.makedirs(deep)
        self.assertEqual(config.find_project(deep), self.proj)
        self.assertIsNone(config.find_project(self.outside))

    def test_project_team_overrides_a_global_team_of_the_same_name(self):
        self.write(self.proj, "teams/dev.toml", WEB_TEAM.replace('"web"', '"dev"'))
        config.set_project(self.proj)
        team = config.load_team("dev", config.load_roles())
        self.assertEqual([m.role for m in team.members], ["planner", "designer", "reviewer"])

    # ---- team-up
    def test_team_up_in_a_project_uses_its_default_team_and_records_it(self):
        code, out, err = self.run_cli("team-up", "--pane", "w1:p1")
        self.assertEqual(code, 0, err)
        self.assertIn("team 'web'", out)
        self.assertEqual(len(self.fake()["panes"]), 3)
        self.assertEqual(self.store.load("w1")["project"], self.proj)

    def test_team_up_outside_a_project_uses_the_global_config(self):
        self.move_lead(self.outside)
        code, out, _ = self.run_cli("team-up", "--pane", "w1:p1")
        self.assertEqual(code, 0)
        self.assertIn("team 'dev'", out)
        self.assertIsNone(self.store.load("w1")["project"])

    def test_team_up_says_when_the_workspace_switches_project(self):
        self.run_cli("team-up", "--pane", "w1:p1")
        self.move_lead(self.outside)
        code, out, _ = self.run_cli("team-up", "--team", "dev", "--pane", "w1:p1")
        self.assertEqual(code, 0)
        self.assertIn("참고", out)
        self.assertIsNone(self.store.load("w1")["project"])

    # ---- commands after team-up follow the team's project, not the cwd
    def test_send_uses_the_teams_project_even_from_elsewhere(self):
        self.run_cli("team-up", "--pane", "w1:p1")
        self.move_lead(self.outside)
        code, _, err = self.run_cli("send", "--role", "designer", "--text", "mock it up", "--pane", "w1:p1")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.prompts(self.pane_of("designer"))[-1], "mock it up")

    def test_dispatch_hook_loads_the_project_from_state(self):
        self.run_cli("team-up", "--pane", "w1:p1")
        code, _, err = self.run_cli("run", "--workflow", "design", "--input", "go", "--pane", "w1:p1")
        self.assertEqual(code, 0, err)
        self.set_status("w1:p1", "working")
        self.run_cli("dispatch", env=self.hook("pane.agent_status_changed", "w1:p1", "working"))
        self.set_status("w1:p1", "done")
        code, out, err = self.run_cli("dispatch", env=self.hook("pane.agent_status_changed", "w1:p1", "done"))
        self.assertEqual(code, 0, err)
        self.assertIn("pending:plan->design", out)

    def test_reapply_uses_each_workspaces_project(self):
        self.run_cli("team-up", "--pane", "w1:p1")
        designer = self.pane_of("designer")
        self.edit_fake(lambda s: [p.update(meta={}) for p in s["panes"].values()])
        code, _, err = self.run_cli("reapply", "--quiet")
        self.assertEqual(code, 0, err)
        self.assertIn("Designer", self.fake()["panes"][designer]["meta"].get("title", ""))

    # ---- writing a project config
    def git_project(self):
        root = os.path.join(self.tmp, "proj2")
        sub = os.path.join(root, "pkg")
        os.makedirs(sub)
        subprocess.run(["git", "-C", root, "init", "-q"], check=True)
        os.chdir(sub)
        return os.path.realpath(root)

    def test_setup_project_writes_into_the_git_root_and_leaves_global_alone(self):
        root = self.git_project()
        with open(os.path.join(self.cfg, "roles.toml")) as f:
            global_roles = f.read()
        with mock.patch.object(setup, "_terminal_input", return_value=scripted(TOSS_DEFAULTS)):
            code, out, err = self.run_cli("setup", "--project")     # global roles exist: no --force needed
        self.assertEqual(code, 0, err)
        target = os.path.join(root, ".herdr-roles")
        self.assertTrue(os.path.exists(os.path.join(target, "roles.toml")))
        self.assertTrue(os.path.exists(os.path.join(target, "teams", "toss-silo.toml")))
        with open(os.path.join(target, "config.toml")) as f:
            self.assertIn('default_team = "toss-silo"', f.read())
        with open(os.path.join(self.cfg, "roles.toml")) as f:
            self.assertEqual(f.read(), global_roles)
        self.assertIn("이 프로젝트", out)

    def test_setup_project_refuses_to_overwrite_without_force(self):
        os.chdir(self.cwd)
        code, _, err = self.run_cli("setup", "--project")
        self.assertEqual(code, 1)
        self.assertIn(self.proj, err)

    def test_init_project_copies_examples_into_the_project(self):
        root = self.git_project()
        code, _, err = self.run_cli("init", "--project")
        self.assertEqual(code, 0, err)
        self.assertTrue(os.path.exists(os.path.join(root, ".herdr-roles", "teams", "dev.toml")))
