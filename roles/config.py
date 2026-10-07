"""Declarative configuration: roles.toml, teams/*.toml, workflows/*.toml, config.toml.

Everything is validated eagerly so that `team-up` / `run` fail *before* any pane is touched.
"""
import functools
import os
import re
import subprocess
import tomllib
from dataclasses import dataclass, field

PLUGIN_ID = "herdr-roles"
DIRECTIONS = ("right", "down")
HANDOFFS = ("last_output", "git_diff", "none")


class ConfigError(ValueError):
    pass


def config_dir():
    # herdr sets HERDR_PLUGIN_CONFIG_DIR only for actions/hooks/plugin panes. A shell running `bin/roles` directly
    # asks herdr instead, so both read the same directory even where herdr keeps it somewhere non-default.
    return (os.environ.get("ROLES_CONFIG_DIR") or os.environ.get("HERDR_PLUGIN_CONFIG_DIR")
            or _herdr_config_dir() or os.path.expanduser(f"~/.config/herdr/plugins/config/{PLUGIN_ID}"))


@functools.cache
def _herdr_config_dir():
    """`herdr plugin config-dir herdr-roles`, or None when herdr is missing, too old or fails."""
    try:
        out = subprocess.run([os.environ.get("HERDR_BIN_PATH") or "herdr", "plugin", "config-dir", PLUGIN_ID],
                             capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return None
    lines = out.stdout.strip().splitlines()
    path = lines[-1].strip() if lines else ""
    return path if out.returncode == 0 and os.path.isabs(path) else None


def state_dir():
    return (os.environ.get("ROLES_STATE_DIR") or os.environ.get("HERDR_PLUGIN_STATE_DIR")
            or os.path.expanduser(f"~/.local/state/herdr/plugins/{PLUGIN_ID}"))


def _load(path):
    try:
        with open(path, "rb") as f:
            return tomllib.load(f)
    except FileNotFoundError:
        raise ConfigError(f"config not found: {path}")
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{path}: {e}")


@dataclass
class Role:
    name: str
    label: str
    badge: str = ""
    agent: str | None = None      # `herdr agent start --kind` value; None => plain shell role
    command: str | None = None    # shell command for non-agent roles
    prompt: str = ""
    agent_args: tuple = ()


@dataclass
class Member:
    role: str
    lead: bool = False
    split: str | None = None
    of: str | None = None
    ratio: float | None = None
    count: int = 1


@dataclass
class Team:
    name: str
    description: str = ""
    workflow: str | None = None
    members: list = field(default_factory=list)

    @property
    def lead(self):
        return next(m for m in self.members if m.lead)


@dataclass
class Step:
    id: str
    role: str
    from_: tuple = ()             # parent step ids; several parents let a step be re-entered (review <- build, fix)
    handoff: str = "last_output"
    auto: bool = False
    template: str = "{{input}}"
    when: str | None = None
    max_iterations: int = 5


@dataclass
class Workflow:
    name: str
    max_hops: int = 12
    steps: list = field(default_factory=list)

    def step(self, sid):
        return next((s for s in self.steps if s.id == sid), None)

    @property
    def entry(self):
        return next(s for s in self.steps if not s.from_)


def load_settings(cdir=None):
    cdir = cdir or config_dir()
    path = os.path.join(cdir, "config.toml")
    data = _load(path).get("settings", {}) if os.path.exists(path) else {}
    return {
        "default_team": data.get("default_team"),
        "default_workflow": data.get("default_workflow"),
        "max_spawn": int(data.get("max_spawn", 8)),
        "handoff_lines": int(data.get("handoff_lines", 200)),
        "spawn_timeout_ms": int(data.get("spawn_timeout_ms", 60000)),
        "idle_grace_s": int(data.get("idle_grace_s", 10)),
    }


def load_roles(cdir=None):
    cdir = cdir or config_dir()
    path = os.path.join(cdir, "roles.toml")
    raw = _load(path).get("roles", {})
    if not raw:
        raise ConfigError(f"{path}: no [roles.*] tables")
    roles = {}
    for name, r in raw.items():
        if r.get("agent") and r.get("command"):
            raise ConfigError(f"role '{name}': set either 'agent' or 'command', not both")
        label = r.get("label") or name.capitalize()
        roles[name] = Role(name=name, label=label, badge=r.get("badge", label[:1]),
                           agent=r.get("agent"), command=r.get("command"), prompt=(r.get("prompt") or "").strip(),
                           agent_args=tuple(r.get("agent_args", ())))
    return roles


def list_names(sub, cdir=None):
    d = os.path.join(cdir or config_dir(), sub)
    if not os.path.isdir(d):
        return []
    return sorted(f[:-5] for f in os.listdir(d) if f.endswith(".toml"))


def load_team(name, roles, cdir=None):
    path = os.path.join(cdir or config_dir(), "teams", f"{name}.toml")
    raw = _load(path)
    t = raw.get("team", {})
    members = []
    for i, m in enumerate(raw.get("member", [])):
        mem = Member(role=m.get("role", ""), lead=bool(m.get("lead", False)), split=m.get("split"),
                     of=m.get("of"), ratio=m.get("ratio"), count=int(m.get("count", 1)))
        if mem.role not in roles:
            raise ConfigError(f"team '{name}': member #{i + 1} role '{mem.role}' is not defined in roles.toml")
        if mem.split and mem.split not in DIRECTIONS:
            raise ConfigError(f"team '{name}': member '{mem.role}' split must be one of {DIRECTIONS}")
        if mem.ratio is not None and not 0 < float(mem.ratio) < 1:
            raise ConfigError(f"team '{name}': member '{mem.role}' ratio must be between 0 and 1")
        if mem.count < 1:
            raise ConfigError(f"team '{name}': member '{mem.role}' count must be >= 1")
        if mem.lead and mem.count != 1:
            raise ConfigError(f"team '{name}': the lead member must have count = 1")
        members.append(mem)
    leads = [m for m in members if m.lead]
    if len(leads) != 1:
        raise ConfigError(f"team '{name}': exactly one member needs lead = true (found {len(leads)})")
    seen = set()
    for m in members:
        if m.of is not None and m.of not in seen:
            raise ConfigError(f"team '{name}': member '{m.role}' places itself 'of' '{m.of}', "
                              f"which must be an earlier member")
        seen.add(m.role)
    return Team(name=t.get("name", name), description=t.get("description", ""), workflow=t.get("workflow"),
                members=members)


def load_workflow(name, roles, cdir=None):
    path = os.path.join(cdir or config_dir(), "workflows", f"{name}.toml")
    raw = _load(path)
    w = raw.get("workflow", {})
    steps = []
    for s in raw.get("step", []):
        parents = s.get("from") or ()
        step = Step(id=s.get("id", ""), role=s.get("role", ""),
                    from_=(parents,) if isinstance(parents, str) else tuple(parents),
                    handoff=s.get("handoff", "last_output"), auto=bool(s.get("auto", False)),
                    template=s.get("template", "{{input}}"), when=s.get("when"),
                    max_iterations=int(s.get("max_iterations", 5)))
        if not step.id:
            raise ConfigError(f"workflow '{name}': a step has no id")
        if step.role not in roles:
            raise ConfigError(f"workflow '{name}': step '{step.id}' role '{step.role}' is not defined in roles.toml")
        if not (step.handoff in HANDOFFS or step.handoff.startswith("file:")):
            raise ConfigError(f"workflow '{name}': step '{step.id}' handoff must be one of {HANDOFFS} or 'file:<path>'")
        if step.when is not None:
            parse_when(step.when)
        steps.append(step)
    ids = [s.id for s in steps]
    if len(set(ids)) != len(ids):
        raise ConfigError(f"workflow '{name}': duplicate step ids")
    for s in steps:
        for parent in s.from_:
            if parent not in ids:
                raise ConfigError(f"workflow '{name}': step '{s.id}' comes from unknown step '{parent}'")
    entries = [s for s in steps if not s.from_]
    if len(entries) != 1:
        raise ConfigError(f"workflow '{name}': exactly one step must have no 'from' (found {len(entries)})")
    return Workflow(name=w.get("name", name), max_hops=int(w.get("max_hops", 12)), steps=steps)


_WHEN = re.compile(r"^\s*output\s*(!?~)\s*/(.*)/\s*$", re.S)


def parse_when(expr):
    """`output ~ /REGEX/` or `output !~ /REGEX/` -> (negate, compiled)."""
    m = _WHEN.match(expr)
    if not m:
        raise ConfigError(f"invalid 'when' expression: {expr!r} (use: output ~ /regex/ or output !~ /regex/)")
    try:
        return m.group(1) == "!~", re.compile(m.group(2), re.M)
    except re.error as e:
        raise ConfigError(f"invalid regex in 'when' {expr!r}: {e}")
