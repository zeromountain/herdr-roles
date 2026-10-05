import os
import shutil
import unittest
from unittest import mock

from helpers import FakeEnv
import test_cli
from roles import cli, config, setup

EXAMPLES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "examples")


def scripted(answers):
    it = iter(answers)
    return lambda _prompt="": next(it)


# planner(claude, with prompt) -> add more -> builder(shell command) -> no more -> lead planner -> team "mini" -> save
TWO_ROLES = ["planner", "", "1", "1", "설계를 맡습니다", "y",
             "builder", "Builder", "2", "pnpm dev", "n",
             "1", "mini", "y"]


class WizardTests(FakeEnv):
    def empty_config(self):
        shutil.rmtree(self.cfg)
        os.makedirs(self.cfg)

    def test_needs_setup_only_when_no_roles_exist(self):
        self.assertFalse(setup.needs_setup())
        self.empty_config()
        self.assertTrue(setup.needs_setup())
        with open(os.path.join(self.cfg, "roles.toml"), "w") as f:
            f.write("# nothing here\n")
        self.assertTrue(setup.needs_setup())

    def test_malformed_roles_file_is_not_treated_as_empty(self):
        with open(os.path.join(self.cfg, "roles.toml"), "w") as f:
            f.write("[roles.x\n")
        self.assertFalse(setup.needs_setup())      # must surface the error, not get overwritten by the wizard

    def test_wizard_writes_loadable_roles_team_and_default(self):
        self.empty_config()
        team = setup.run_wizard(ask=scripted(TWO_ROLES), say=lambda *_: None)
        self.assertEqual(team, "mini")
        roles = config.load_roles()
        self.assertEqual(roles["planner"].agent, "claude")
        self.assertEqual(roles["planner"].prompt, "설계를 맡습니다")
        self.assertEqual(roles["builder"].command, "pnpm dev")
        self.assertIsNone(roles["builder"].agent)
        loaded = config.load_team("mini", roles)
        self.assertEqual(loaded.lead.role, "planner")
        self.assertEqual([m.role for m in loaded.members], ["planner", "builder"])
        self.assertEqual(config.load_settings()["default_team"], "mini")

    def test_special_characters_survive_round_trip(self):
        self.empty_config()
        prompt = 'say "hi" \\ done'
        setup.run_wizard(ask=scripted(["r", "", "1", "1", prompt, "n", "t", "y"]), say=lambda *_: None)
        self.assertEqual(config.load_roles()["r"].prompt, prompt)

    def test_invalid_and_duplicate_answers_are_asked_again(self):
        self.empty_config()
        said = []
        # "Bad Name" rejected; second "dup" rejected as duplicate; lead choice "9" rejected
        answers = ["Bad Name", "dup", "", "3", "y", "dup", "other", "", "3", "n", "9", "1", "", "y"]
        setup.run_wizard(ask=scripted(answers), say=said.append)
        self.assertEqual(sorted(config.load_roles()), ["dup", "other"])
        self.assertTrue(any("소문자" in s for s in said))
        self.assertTrue(any("이미 추가" in s for s in said))
        self.assertTrue(any("목록에 있는 번호" in s for s in said))

    def test_declining_confirmation_writes_nothing(self):
        self.empty_config()
        with self.assertRaises(setup.SetupAborted):
            setup.run_wizard(ask=scripted(TWO_ROLES[:-1] + ["n"]), say=lambda *_: None)
        self.assertEqual(os.listdir(self.cfg), [])

    def test_existing_default_team_is_kept_when_it_still_exists(self):
        os.remove(os.path.join(self.cfg, "roles.toml"))
        setup.run_wizard(ask=scripted(TWO_ROLES), say=lambda *_: None)
        self.assertEqual(config.load_settings()["default_team"], "dev")

    def test_stale_default_team_is_repointed_and_other_settings_kept(self):
        os.remove(os.path.join(self.cfg, "roles.toml"))
        os.remove(os.path.join(self.cfg, "teams", "dev.toml"))
        setup.run_wizard(ask=scripted(TWO_ROLES), say=lambda *_: None)
        s = config.load_settings()
        self.assertEqual(s["default_team"], "mini")
        self.assertEqual(s["max_spawn"], 8)


class FirstRunCliTests(FakeEnv):
    """`team-up` with no roles configured."""

    run_cli = test_cli.CliTests.run_cli

    def setUp(self):
        super().setUp()
        shutil.rmtree(self.cfg)
        os.makedirs(self.cfg)

    def with_examples(self):
        shutil.copytree(EXAMPLES, self.cfg, dirs_exist_ok=True)

    def test_without_a_terminal_it_does_not_hang_and_points_to_setup(self):
        code, _, err = self.run_cli("team-up", "--pane", "w1:p1")
        self.assertEqual(code, 1)
        self.assertIn("설정", err)
        self.assertEqual(len(self.fake()["panes"]), 1)

    def test_with_a_terminal_it_asks_then_builds_the_team(self):
        with mock.patch.object(cli, "_has_terminal", return_value=True), \
                mock.patch("builtins.input", side_effect=scripted(TWO_ROLES)):
            code, out, _ = self.run_cli("team-up", "--pane", "w1:p1")
        self.assertEqual(code, 0)
        self.assertIn("team 'mini'", out)
        self.assertEqual(len(self.fake()["panes"]), 2)       # lead + builder

    def test_aborting_the_wizard_exits_nonzero_and_spawns_nothing(self):
        with mock.patch.object(cli, "_has_terminal", return_value=True), \
                mock.patch("builtins.input", side_effect=scripted(TWO_ROLES[:-1] + ["n"])):
            code, _, _ = self.run_cli("team-up", "--pane", "w1:p1")
        self.assertEqual(code, 1)
        self.assertEqual(len(self.fake()["panes"]), 1)

    def test_configured_roles_skip_the_wizard(self):
        self.with_examples()
        with mock.patch("builtins.input", side_effect=AssertionError("must not ask")):
            code, _, _ = self.run_cli("team-up", "--team", "dev", "--pane", "w1:p1")
        self.assertEqual(code, 0)

    def test_setup_command_refuses_to_overwrite_without_force(self):
        self.with_examples()
        code, _, err = self.run_cli("setup")
        self.assertEqual(code, 1)
        self.assertIn("--force", err)


if __name__ == "__main__":
    unittest.main()
