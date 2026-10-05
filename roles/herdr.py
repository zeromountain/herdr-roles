"""Thin wrapper over the herdr CLI (the CLI is the plugin API; there is no SDK).

Every call goes through `$HERDR_BIN_PATH` (or `herdr`) and inherits the environment, so a hook or a pane
shell automatically talks to the session that produced the event (`HERDR_SOCKET_PATH`).
"""
import json
import os
import subprocess


class HerdrError(RuntimeError):
    pass


class Herdr:
    def __init__(self, binary=None, env=None):
        b = binary or os.environ.get("HERDR_BIN_PATH") or "herdr"
        self.bin = list(b) if isinstance(b, (list, tuple)) else [b]
        self.env = env

    def _run(self, args, timeout):
        cmd = [*self.bin, *[str(a) for a in args]]
        try:
            return subprocess.run(cmd, capture_output=True, text=True, env=self.env, timeout=timeout)
        except FileNotFoundError as e:
            raise HerdrError(f"herdr binary not found: {self.bin[0]}") from e
        except subprocess.TimeoutExpired as e:
            raise HerdrError(f"timeout: {' '.join(map(str, args))}") from e

    def call(self, *args, timeout=120):
        """Run a command that prints the JSON envelope; return its `result`."""
        p = self._run(args, timeout)
        out = p.stdout.strip()
        try:
            data = json.loads(out) if out else {}
        except json.JSONDecodeError:
            raise HerdrError(f"{' '.join(map(str, args[:3]))}: non-JSON output: {out[:200]!r} {p.stderr.strip()[:200]}")
        if isinstance(data, dict) and "error" in data:
            err = data["error"]
            raise HerdrError(f"{' '.join(map(str, args[:3]))}: {err.get('message', err) if isinstance(err, dict) else err}")
        if p.returncode != 0:
            raise HerdrError(f"{' '.join(map(str, args[:3]))}: exit {p.returncode}: {p.stderr.strip()[:200]}")
        return data.get("result", data) if isinstance(data, dict) else data

    def text(self, *args, timeout=60):
        """Run a command whose stdout is plain text (pane read / agent read)."""
        p = self._run(args, timeout)
        if p.returncode != 0:
            raise HerdrError(f"{' '.join(map(str, args[:3]))}: exit {p.returncode}: {p.stderr.strip()[:200] or p.stdout.strip()[:200]}")
        out = p.stdout
        s = out.strip()
        if s.startswith("{"):
            try:
                r = json.loads(s).get("result", {})
                for k in ("text", "output", "content"):
                    if isinstance(r.get(k), str):
                        return r[k]
                read = r.get("read")
                if isinstance(read, dict) and isinstance(read.get("text"), str):
                    return read["text"]
            except (json.JSONDecodeError, AttributeError):
                pass
        return out

    # ---- panes
    def pane_list(self, workspace=None):
        args = ["pane", "list"] + (["--workspace", workspace] if workspace else [])
        return self.call(*args).get("panes", [])

    def pane_get(self, pane_id):
        return self.call("pane", "get", pane_id).get("pane", {})

    def pane_split(self, pane_id, direction, ratio=None, cwd=None):
        args = ["pane", "split", "--pane", pane_id, "--direction", direction]
        if ratio is not None:
            args += ["--ratio", f"{ratio:.4f}"]
        if cwd:
            args += ["--cwd", cwd]
        return self.call(*args)["pane"]["pane_id"]

    def pane_close(self, pane_id):
        self.call("pane", "close", pane_id)

    def pane_run(self, pane_id, command):
        self.call("pane", "run", pane_id, command)

    def pane_send_text(self, pane_id, text):
        self.call("pane", "send-text", pane_id, text)

    def pane_rename(self, pane_id, label):
        self.call("pane", "rename", pane_id, label)

    def pane_read(self, pane_id, lines=200, source="recent"):
        return self.text("pane", "read", pane_id, "--source", source, "--lines", lines)

    def report_metadata(self, pane_id, source, title=None, tokens=None, state_labels=None, seq=None):
        args = ["pane", "report-metadata", pane_id, "--source", source]
        if title is not None:
            args += ["--title", title]
        for k, v in (tokens or {}).items():
            args += ["--token", f"{k}={v}"]
        for st, label in (state_labels or {}).items():
            args += ["--state-label", f"{st}={label}"]
        if seq is not None:
            args += ["--seq", seq]
        self.call(*args)

    # ---- agents
    def agent_start(self, name, kind, pane_id, timeout_ms=None, agent_args=()):
        args = ["agent", "start", name, "--kind", kind, "--pane", pane_id]
        if timeout_ms:
            args += ["--timeout", timeout_ms]
        if agent_args:
            args += ["--", *agent_args]
        return self.call(*args, timeout=(timeout_ms or 30000) / 1000 + 30)["agent"]

    def agent_prompt(self, target, text, wait=False, until=(), timeout_ms=None):
        """Submit a prompt to the agent in `target` (a pane id works for agents herdr merely detected, too).

        Deliberately no fallback to typing into the pane: if the agent is gone the pane is a plain shell, and
        typing a handoff payload there would execute it. Errors propagate; `team-up` revives dead agents.
        """
        args = ["agent", "prompt", target, text]
        if wait:
            args.append("--wait")
            for u in until:
                args += ["--until", u]
            if timeout_ms:
                args += ["--timeout", timeout_ms]
        return self.call(*args, timeout=((timeout_ms or 120000) / 1000 + 30) if wait else 60)

    # ---- misc
    def notify(self, title, body=None, sound=None):
        args = ["notification", "show", title]
        if body:
            args += ["--body", body]
        if sound:
            args += ["--sound", sound]
        try:
            self.call(*args)
        except HerdrError:
            pass  # notifications are best-effort

    def plugin_pane_open(self, plugin_id, entrypoint, target_pane=None):
        args = ["plugin", "pane", "open", "--plugin", plugin_id, "--entrypoint", entrypoint]
        if target_pane:
            args += ["--pane", target_pane]
        return self.call(*args)
