"""Shared fixtures: temp config/state dirs, a fake herdr, and small helpers to poke its state."""
import contextlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

from roles import config
from roles.herdr import Herdr
from roles.state import Store

HERE = os.path.dirname(os.path.abspath(__file__))
FAKE = os.path.join(HERE, "fake_herdr.py")
EXAMPLES = os.path.join(HERE, "..", "examples")


class FakeEnv(unittest.TestCase):
    """Base class: one workspace `w1` with a single lead pane `w1:p1` (a claude agent, idle)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="roles-test-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.cfg = os.path.join(self.tmp, "config")
        shutil.copytree(EXAMPLES, self.cfg)
        self.fake_path = os.path.join(self.tmp, "fake.json")
        patch = {"ROLES_CONFIG_DIR": self.cfg, "ROLES_STATE_DIR": os.path.join(self.tmp, "state"),
                 "FAKE_HERDR_STATE": self.fake_path,
                 # the wizard links `roles` and the agent skill under the home dir: keep that out of the real one
                 "ROLES_HOME": os.path.join(self.tmp, "home"), "CODEX_HOME": os.path.join(self.tmp, "home", ".codex")}
        for k, v in patch.items():
            self._set_env(k, v)
        self._set_env("HERDR_SESSION", None)
        self._set_env("ROLES_BIN_DIR", None)
        self._set_env("SHELL", None)    # install's PATH check would otherwise start the user's login shell
        self.cwd = os.path.join(self.tmp, "repo")
        os.makedirs(self.cwd)
        self.write_fake({"panes": {"w1:p1": self._pane("w1:p1", agent="claude", status="idle")},
                         "next": {"w1": 1}, "notifications": [], "calls": [], "splits": []})
        self.h = Herdr(binary=[sys.executable, FAKE])
        self.store = Store()
        self.roles = config.load_roles()
        self.settings = config.load_settings()
        self.team = config.load_team("dev", self.roles)

    def _set_env(self, k, v):
        old = os.environ.get(k)
        self.addCleanup(lambda: os.environ.pop(k, None) if old is None else os.environ.__setitem__(k, old))
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v

    def _pane(self, pid, agent=None, status="unknown"):
        return {"pane_id": pid, "workspace_id": pid.split(":")[0], "cwd": self.cwd, "agent": agent,
                "agent_status": status, "output": "", "ran": [], "prompts": [], "meta": {}}

    # ---- fake state access
    def fake(self):
        with open(self.fake_path) as f:
            return json.load(f)

    def write_fake(self, s):
        with open(self.fake_path, "w") as f:
            json.dump(s, f)

    def edit_fake(self, fn):
        s = self.fake()
        fn(s)
        self.write_fake(s)

    def set_status(self, pane, status):
        self.edit_fake(lambda s: s["panes"][pane].update(agent_status=status))

    def set_output(self, pane, text):
        self.edit_fake(lambda s: s["panes"][pane].update(output=text))

    def calls(self, prefix):
        return [c for c in self.fake()["calls"] if " ".join(c[:2]) == prefix]

    def pane_of(self, role, data=None):
        data = data or self.store.load("w1")
        return next(pid for pid, p in data["panes"].items() if p["role"] == role)

    def prompts(self, pane):
        return self.fake()["panes"][pane]["prompts"]

    def git_repo_with_change(self):
        run = lambda *a: subprocess.run(["git", "-C", self.cwd, *a], check=True, capture_output=True)  # noqa: E731
        run("init", "-q")
        run("config", "user.email", "t@t")
        run("config", "user.name", "t")
        path = os.path.join(self.cwd, "a.txt")
        with open(path, "w") as f:
            f.write("one\n")
        run("add", ".")
        run("commit", "-qm", "init")
        with open(path, "w") as f:
            f.write("two\n")
