"""`roles install` and the wizard's post-save step: link the CLI onto PATH and the skill into agent homes."""
import io
import os
import shutil
import sys
import unittest
from contextlib import redirect_stdout
from unittest import mock

from helpers import FakeEnv
from roles import cli, install, setup
from test_setup import TOSS_DEFAULTS, scripted


class InstallTests(FakeEnv):
    def setUp(self):
        super().setUp()
        self.home = os.environ["ROLES_HOME"]
        self.bin = os.path.join(self.home, ".local", "bin")
        self.link = os.path.join(self.bin, "roles")
        self._set_env("PATH", self.bin + os.pathsep + os.environ.get("PATH", ""))

    def agent_homes(self, *names):
        for n in names:
            os.makedirs(os.path.join(self.home, n), exist_ok=True)

    def run_cli(self, *argv):
        out = io.StringIO()
        with redirect_stdout(out):
            rc = cli.main(list(argv), h=self.h)
        return rc, out.getvalue()

    def test_links_the_cli_and_skills_into_existing_agent_homes(self):
        self.agent_homes(".claude", ".codex")
        rc, out = self.run_cli("install")
        self.assertEqual(rc, 0)
        self.assertEqual(os.path.realpath(self.link), os.path.realpath(install.CLI))
        for agent in (".claude", ".codex"):
            skill = os.path.join(self.home, agent, "skills", "herdr-roles")
            self.assertEqual(os.path.realpath(skill), os.path.realpath(install.SKILL))
            self.assertTrue(os.path.isfile(os.path.join(skill, "SKILL.md")))
        self.assertNotIn("PATH 에 없어", out)

    def test_skips_agents_that_are_not_installed(self):
        self.agent_homes(".claude")
        _, out = self.run_cli("install")
        self.assertIn("Claude Code 스킬", out)
        self.assertNotIn("Codex", out)
        self.assertFalse(os.path.exists(os.path.join(self.home, ".codex")))

    def test_codex_home_env_is_respected(self):
        codex = os.path.join(self.tmp, "elsewhere-codex")
        os.makedirs(codex)
        self._set_env("CODEX_HOME", codex)
        self.run_cli("install")
        self.assertTrue(os.path.islink(os.path.join(codex, "skills", "herdr-roles")))

    def test_rerun_is_a_noop(self):
        self.run_cli("install")
        _, out = self.run_cli("install")
        self.assertIn("이미 연결돼 있어요", out)

    def test_keeps_a_link_to_another_checkout_unless_forced(self):
        other = os.path.join(self.tmp, "clone", "bin", "roles")
        os.makedirs(os.path.dirname(other))
        open(other, "w").close()
        os.makedirs(self.bin)
        os.symlink(other, self.link)
        _, out = self.run_cli("install")
        self.assertIn("그대로 뒀어요", out)
        self.assertEqual(os.path.realpath(self.link), os.path.realpath(other))
        self.run_cli("install", "--force")
        self.assertEqual(os.path.realpath(self.link), os.path.realpath(install.CLI))

    def test_replaces_a_dangling_link(self):
        os.makedirs(self.bin)
        os.symlink(os.path.join(self.tmp, "gone", "roles"), self.link)
        self.run_cli("install")
        self.assertEqual(os.path.realpath(self.link), os.path.realpath(install.CLI))

    def test_never_overwrites_a_real_file(self):
        os.makedirs(self.bin)
        with open(self.link, "w") as f:
            f.write("mine")
        rc, out = self.run_cli("install", "--force")
        self.assertEqual(rc, 0)
        self.assertIn("링크가 아닌 파일", out)
        with open(self.link) as f:
            self.assertEqual(f.read(), "mine")

    def test_warns_when_the_bin_dir_is_not_on_path(self):
        self._set_env("PATH", "/usr/bin:/bin")
        _, out = self.run_cli("install")
        self.assertIn("PATH 에 없어", out)
        self.assertIn(".zprofile", out)

    def test_login_shell_path_counts(self):
        self._set_env("PATH", "/usr/bin:/bin")
        fake_shell = os.path.join(self.tmp, "fake-shell")
        with open(fake_shell, "w") as f:
            f.write(f"#!/bin/sh\nprintf %s '{self.bin}:/usr/bin'\n")
        os.chmod(fake_shell, 0o755)
        self._set_env("SHELL", fake_shell)
        _, out = self.run_cli("install")
        self.assertNotIn("PATH 에 없어", out)

    def test_wizard_installs_after_saving(self):
        self.agent_homes(".claude")
        shutil.rmtree(self.cfg)
        os.makedirs(self.cfg)
        with mock.patch.object(setup, "_terminal_input", return_value=scripted(TOSS_DEFAULTS)), \
                mock.patch.object(cli, "_has_terminal", return_value=False):
            rc, out = self.run_cli("setup")
        self.assertEqual(rc, 0)
        self.assertTrue(os.path.islink(self.link))
        self.assertTrue(os.path.islink(os.path.join(self.home, ".claude", "skills", "herdr-roles")))
        self.assertIn("에이전트에서 쓸 수 있게 연결", out)

    def test_aborted_wizard_installs_nothing(self):
        shutil.rmtree(self.cfg)
        os.makedirs(self.cfg)
        answers = TOSS_DEFAULTS[:-1] + ["n"]
        with mock.patch.object(setup, "_terminal_input", return_value=scripted(answers)), \
                mock.patch.object(sys, "stderr", io.StringIO()):
            rc, _ = self.run_cli("setup")
        self.assertEqual(rc, 1)
        self.assertFalse(os.path.lexists(self.link))


class SkillFileTests(unittest.TestCase):
    def test_frontmatter_triggers_on_what_users_type(self):
        with open(os.path.join(install.SKILL, "SKILL.md"), encoding="utf-8") as f:
            head = f.read().split("---")[1]
        self.assertIn("name: herdr-roles", head)
        for phrase in ("roles team-up", "팀 구성", "herdr-roles"):
            self.assertIn(phrase, head)


if __name__ == "__main__":
    unittest.main()
