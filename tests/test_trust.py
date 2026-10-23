"""trust_folders: a team's folder is marked trusted for Claude Code and Codex before their agents start."""
import json
import os

from helpers import FakeEnv
from roles import config, team as teammod, trust


class TrustTests(FakeEnv):
    def claude_projects(self):
        with open(os.path.join(os.environ["ROLES_HOME"], ".claude.json")) as f:
            return json.load(f)["projects"]

    def codex_config(self):
        with open(os.path.join(os.environ["CODEX_HOME"], "config.toml")) as f:
            return f.read()

    def up(self, trust_folders):
        settings = {**self.settings, "trust_folders": trust_folders}
        return teammod.team_up(self.h, self.store, self.roles, self.team, "w1:p1", settings)

    def test_off_by_default(self):
        self.assertFalse(config.load_settings()["trust_folders"])
        self.up(False)
        self.assertFalse(os.path.exists(os.path.join(os.environ["ROLES_HOME"], ".claude.json")))

    def test_team_up_trusts_the_folder_for_every_agent_kind(self):
        rep = self.up(True)
        folder = os.path.realpath(self.cwd)
        self.assertEqual(rep["trusted"], ["claude", "codex"])
        self.assertTrue(self.claude_projects()[folder]["hasTrustDialogAccepted"])
        self.assertIn(f'[projects."{folder}"]\ntrust_level = "trusted"', self.codex_config())

    def test_existing_settings_are_kept_and_decisions_respected(self):
        folder = os.path.realpath(self.cwd)
        os.makedirs(os.environ["CODEX_HOME"])
        with open(os.path.join(os.environ["ROLES_HOME"], ".claude.json"), "w") as f:
            json.dump({"numStartups": 7, "projects": {"/other": {"x": 1}}}, f)
        with open(os.path.join(os.environ["CODEX_HOME"], "config.toml"), "w") as f:
            f.write(f'model = "o"\n[projects."{folder}"]\ntrust_level = "untrusted"\n')
        self.assertEqual(trust.ensure(self.cwd, ["claude", "codex", "gemini"]), ["claude"])
        with open(os.path.join(os.environ["ROLES_HOME"], ".claude.json")) as f:
            data = json.load(f)
        self.assertEqual((data["numStartups"], data["projects"]["/other"]), (7, {"x": 1}))
        self.assertIn('trust_level = "untrusted"', self.codex_config())     # the user's own choice stays
        self.assertEqual(trust.ensure(self.cwd, ["claude"]), [])             # already trusted: no rewrite

    def test_an_unreadable_claude_file_is_never_overwritten(self):
        path = os.path.join(os.environ["ROLES_HOME"], ".claude.json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write("{broken")
        self.assertEqual(trust.ensure(self.cwd, ["claude"]), [])
        with open(path) as f:
            self.assertEqual(f.read(), "{broken")
