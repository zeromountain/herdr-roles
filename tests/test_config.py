import os
import shutil
import tempfile
import unittest

from helpers import FakeEnv
from roles import config
from roles.config import ConfigError


class ConfigDirTests(unittest.TestCase):
    """Without HERDR_PLUGIN_CONFIG_DIR (a plain shell), the config dir comes from `herdr plugin config-dir`."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="roles-cfgdir-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        for k in ("ROLES_CONFIG_DIR", "HERDR_PLUGIN_CONFIG_DIR", "HERDR_BIN_PATH"):
            old = os.environ.pop(k, None)
            if old is not None:
                self.addCleanup(os.environ.__setitem__, k, old)
        self.addCleanup(os.environ.pop, "HERDR_BIN_PATH", None)
        config._herdr_config_dir.cache_clear()
        self.addCleanup(config._herdr_config_dir.cache_clear)

    def fake_herdr(self, body):
        path = os.path.join(self.tmp, "herdr")
        with open(path, "w") as f:
            f.write(f"#!/bin/sh\n{body}\n")
        os.chmod(path, 0o755)
        os.environ["HERDR_BIN_PATH"] = path

    def test_asks_herdr_for_the_plugin_config_dir(self):
        self.fake_herdr('[ "$1 $2 $3" = "plugin config-dir herdr-roles" ] && echo /custom/herdr-roles')
        self.assertEqual(config.config_dir(), "/custom/herdr-roles")

    def test_falls_back_when_herdr_fails_or_is_missing(self):
        default = os.path.expanduser("~/.config/herdr/plugins/config/herdr-roles")
        self.fake_herdr("echo 'unknown command' >&2; exit 2")
        self.assertEqual(config.config_dir(), default)
        config._herdr_config_dir.cache_clear()
        os.environ["HERDR_BIN_PATH"] = os.path.join(self.tmp, "no-such-herdr")
        self.assertEqual(config.config_dir(), default)

    def test_environment_wins_without_calling_herdr(self):
        self.fake_herdr("exit 99")
        os.environ["HERDR_PLUGIN_CONFIG_DIR"] = "/from/herdr/env"
        self.addCleanup(os.environ.pop, "HERDR_PLUGIN_CONFIG_DIR", None)
        self.assertEqual(config.config_dir(), "/from/herdr/env")
        self.assertEqual(config._herdr_config_dir.cache_info().currsize, 0)


class ConfigTests(FakeEnv):
    def write(self, rel, text):
        path = os.path.join(self.cfg, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(text)

    def test_examples_load(self):
        team = config.load_team("dev", self.roles)
        self.assertEqual([m.role for m in team.members], ["planner", "implementer", "reviewer", "tester", "runner"])
        self.assertEqual(team.lead.role, "planner")
        wf = config.load_workflow("feature", self.roles)
        self.assertEqual(wf.entry.id, "plan")
        self.assertEqual(config.list_names("teams"), ["dev"])

    def test_step_may_have_several_parents(self):
        wf = config.load_workflow("feature", self.roles)
        self.assertEqual(wf.step("review").from_, ("build", "fix"))
        self.assertEqual(wf.step("build").from_, ("plan",))
        self.assertEqual(wf.step("plan").from_, ())

    def test_role_cannot_have_agent_and_command(self):
        self.write("roles.toml", '[roles.x]\nagent = "claude"\ncommand = "ls"\n')
        with self.assertRaisesRegex(ConfigError, "either 'agent' or 'command'"):
            config.load_roles()

    def test_team_needs_exactly_one_lead(self):
        self.write("teams/t.toml", '[[member]]\nrole = "planner"\n')
        with self.assertRaisesRegex(ConfigError, "exactly one member"):
            config.load_team("t", self.roles)
        self.write("teams/t.toml", '[[member]]\nrole = "planner"\nlead = true\n[[member]]\nrole = "tester"\nlead = true\n')
        with self.assertRaisesRegex(ConfigError, "exactly one member"):
            config.load_team("t", self.roles)

    def test_team_validation_errors(self):
        cases = {
            "not defined in roles.toml": '[[member]]\nrole = "ghost"\nlead = true\n',
            "earlier member": '[[member]]\nrole = "planner"\nlead = true\n[[member]]\nrole = "tester"\nof = "runner"\n'
                              '[[member]]\nrole = "runner"\n',
            "ratio must be between": '[[member]]\nrole = "planner"\nlead = true\n[[member]]\nrole = "tester"\nratio = 1.5\n',
            "split must be one of": '[[member]]\nrole = "planner"\nlead = true\n[[member]]\nrole = "tester"\nsplit = "up"\n',
            "count must be >= 1": '[[member]]\nrole = "planner"\nlead = true\n[[member]]\nrole = "tester"\ncount = 0\n',
            "lead member must have count": '[[member]]\nrole = "planner"\nlead = true\ncount = 2\n',
        }
        for msg, body in cases.items():
            with self.subTest(msg):
                self.write("teams/bad.toml", body)
                with self.assertRaisesRegex(ConfigError, msg):
                    config.load_team("bad", self.roles)

    def test_workflow_validation_errors(self):
        step = lambda extra="": f'[[step]]\nid = "a"\nrole = "planner"\n{extra}'  # noqa: E731
        cases = {
            "unknown step": step() + '[[step]]\nid = "b"\nrole = "tester"\nfrom = "zzz"\n',
            "exactly one step must have no 'from'": step() + '[[step]]\nid = "b"\nrole = "tester"\n',
            "duplicate step ids": step() + '[[step]]\nid = "a"\nrole = "tester"\nfrom = "a"\n',
            "handoff must be one of": step('handoff = "mind-meld"\n'),
            "invalid 'when'": step() + '[[step]]\nid = "b"\nrole = "tester"\nfrom = "a"\nwhen = "banana"\n',
            "not defined in roles.toml": '[[step]]\nid = "a"\nrole = "ghost"\n',
        }
        for msg, body in cases.items():
            with self.subTest(msg):
                self.write("workflows/bad.toml", body)
                with self.assertRaisesRegex(ConfigError, msg):
                    config.load_workflow("bad", self.roles)

    def test_parse_when(self):
        neg, rx = config.parse_when("output ~ /CHANGES/")
        self.assertFalse(neg)
        self.assertTrue(rx.search("x\nCHANGES\n"))
        neg, _ = config.parse_when("output !~ /OK/")
        self.assertTrue(neg)
        with self.assertRaises(ConfigError):
            config.parse_when("output ~ /(/")

    def test_settings_defaults_and_override(self):
        self.assertEqual(self.settings["max_spawn"], 8)
        os.remove(os.path.join(self.cfg, "config.toml"))
        self.assertEqual(config.load_settings()["handoff_lines"], 200)


if __name__ == "__main__":
    unittest.main()
