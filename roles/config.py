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
PROJECT_DIRNAME = ".herdr-roles"
DIRECTIONS = ("right", "down")
HANDOFFS = ("last_output", "git_diff", "none")


class ConfigError(ValueError):
    pass


# The project layer (<repo>/.herdr-roles/) sits on top of the global config dir. The CLI picks it once per command
# (from the pane's cwd, or from the workspace state for hooks, which run without a cwd) and loaders read it from here.
_project = None


def set_project(path):
    global _project
    _project = path or None


def project_dir():
    """Active project config dir. ROLES_PROJECT_DIR overrides (empty string = no project layer)."""
    env = os.environ.get("ROLES_PROJECT_DIR")
    if env is not None:
        return env or None
    return _project


def find_project(start):
    """Nearest `.herdr-roles/` directory at or above `start`, or None."""
    if not start:
        return None
    d = os.path.abspath(start)
    while True:
        cand = os.path.join(d, PROJECT_DIRNAME)
        if os.path.isdir(cand):
            return cand
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent


def project_root(start):
    """Where `--project` creates `.herdr-roles/`: an existing one's parent, else the git top level, else `start`."""
    found = find_project(start)
    if found:
        return os.path.dirname(found)
    try:
        out = subprocess.run(["git", "-C", start, "rev-parse", "--show-toplevel"], capture_output=True, text=True,
                             timeout=5)
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    return os.path.abspath(start)


def layers(cdir=None):
    """Config dirs to read, lowest priority first. An explicit `cdir` means exactly that one dir."""
    if cdir:
        return [cdir]
    project = project_dir()
    return [config_dir()] + ([project] if project and os.path.isdir(project) else [])


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
    data = {}
    for d in layers(cdir):      # later layers win key by key
        path = os.path.join(d, "config.toml")
        if os.path.exists(path):
            data.update(_load(path).get("settings", {}))
    return {
        "default_team": data.get("default_team"),
        "default_workflow": data.get("default_workflow"),
        "max_spawn": int(data.get("max_spawn", 8)),
        "handoff_lines": int(data.get("handoff_lines", 200)),
        "spawn_timeout_ms": int(data.get("spawn_timeout_ms", 60000)),
        "idle_grace_s": int(data.get("idle_grace_s", 10)),
    }


def load_roles(cdir=None):
    """roles.toml of every layer, merged by role name (a project role replaces the global one of the same name)."""
    paths = [os.path.join(d, "roles.toml") for d in layers(cdir)]
    present = [p for p in paths if os.path.exists(p)]
    if not present:
        raise ConfigError(f"config not found: {' / '.join(paths)}")
    raw = {}
    for path in present:
        raw.update(_load(path).get("roles", {}))
    if not raw:
        raise ConfigError(f"{' / '.join(present)}: no [roles.*] tables")
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
    names = set()
    for layer in layers(cdir):
        d = os.path.join(layer, sub)
        if os.path.isdir(d):
            names.update(f[:-5] for f in os.listdir(d) if f.endswith(".toml"))
    return sorted(names)


def _find(sub, name, cdir):
    """Path of `<sub>/<name>.toml` in the highest-priority layer that has it (the project's, if any)."""
    candidates = [os.path.join(d, sub, f"{name}.toml") for d in reversed(layers(cdir))]
    return next((p for p in candidates if os.path.exists(p)), candidates[0])


def load_team(name, roles, cdir=None):
    path = _find("teams", name, cdir)
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
    path = _find("workflows", name, cdir)
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
