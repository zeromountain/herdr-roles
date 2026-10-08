"""Per-session, per-workspace state files.

The plugin registry and state dir are global in herdr: hooks fire for the default session *and* named
sessions, so every file is keyed by `${HERDR_SESSION:-default}`.
"""
import contextlib
import errno
import fcntl
import json
import os
import time

from .config import state_dir


def session_key():
    return os.environ.get("HERDR_SESSION") or "default"


def now_ms():
    return int(time.time() * 1000)


class LockTimeout(RuntimeError):
    pass


class Store:
    def __init__(self, root=None, session=None):
        self.root = root or state_dir()
        self.session = session or session_key()
        self.dir = os.path.join(self.root, self.session)

    def path(self, ws):
        return os.path.join(self.dir, f"{ws.replace('/', '_')}.json")

    def exists(self, ws):
        return os.path.exists(self.path(ws))

    def workspaces(self):
        if not os.path.isdir(self.dir):
            return []
        return sorted(f[:-5] for f in os.listdir(self.dir) if f.endswith(".json"))

    @contextlib.contextmanager
    def lock(self, timeout=None):
        os.makedirs(self.dir, exist_ok=True)
        fd = os.open(os.path.join(self.dir, ".lock"), os.O_CREAT | os.O_RDWR, 0o644)
        try:
            deadline = None if timeout is None else time.time() + timeout
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | (0 if deadline is None else fcntl.LOCK_NB))
                    break
                except OSError as e:
                    if e.errno not in (errno.EAGAIN, errno.EACCES):
                        raise
                    if time.time() >= deadline:
                        raise LockTimeout(f"state lock busy ({self.dir})")
                    time.sleep(0.05)
            yield
        finally:
            os.close(fd)  # closing releases the flock

    def load(self, ws):
        try:
            with open(self.path(ws)) as f:
                data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            data = {}
        data.setdefault("version", 1)
        data.setdefault("session", self.session)
        data.setdefault("workspace", ws)
        data.setdefault("seq", 0)
        data.setdefault("team", None)
        data.setdefault("project", None)     # the team's project config dir; hooks have no cwd to find it from
        data.setdefault("panes", {})
        data.setdefault("run", None)
        data.setdefault("pending_roster", None)
        return data

    def save(self, ws, data):
        os.makedirs(self.dir, exist_ok=True)
        tmp = self.path(ws) + f".tmp{os.getpid()}"
        with open(tmp, "w") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, self.path(ws))


def next_seq(data):
    """Monotonic per-workspace counter; also used as `--seq` so out-of-order metadata is ignored by herdr."""
    data["seq"] = max(int(data.get("seq", 0)) + 1, now_ms())
    return data["seq"]


def panes_with_role(data, role):
    return [pid for pid, p in data["panes"].items() if p.get("role") == role]
