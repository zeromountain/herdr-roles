import os
import unittest

from helpers import FakeEnv
from roles import config
from roles.config import ConfigError


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
