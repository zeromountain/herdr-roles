"""`bin/roles` subcommands. Also the target of every manifest command (actions, events, startup, panes)."""
import argparse
import json
import os
import shutil
import sys
import time

from . import config, display, fleet as fleetmod, handoff, install as installmod, mail, setup as setupmod
from . import team as teammod
from . import workflow as wfmod
from .config import ConfigError, PLUGIN_ID
from .herdr import Herdr, HerdrError
from .setup import SetupAborted
from .state import LockTimeout, Store

EXAMPLES = os.path.join(teammod.ROOT, "examples")


class CliError(RuntimeError):
    pass


def _context_pane():
    """Pane to act on: explicit > plugin context's focused pane > the shell's own pane."""
    raw = os.environ.get("HERDR_PLUGIN_CONTEXT_JSON")
    if raw:
        try:
            pane = json.loads(raw).get("focused_pane_id")
            if pane:
                return pane
        except json.JSONDecodeError:
            pass
    return os.environ.get("HERDR_PANE_ID")


def _where(h, args):
    pane = getattr(args, "pane", None) or _context_pane()
    if not pane:
        raise CliError("대상 pane을 알 수 없습니다. herdr pane 안에서 실행하거나 --pane 을 지정하세요.")
    info = h.pane_get(pane)
    if not info:
        raise CliError(f"pane {pane} 을(를) 찾을 수 없습니다.")
    return pane, info["workspace_id"]


def _pick_team_name(args, settings):
    name = getattr(args, "team", None) or settings["default_team"]
    if name:
        return name
    names = config.list_names("teams")
    if len(names) == 1:
        return names[0]
    raise CliError(f"--team 이 필요합니다 (사용 가능: {', '.join(names) or '없음 — `roles init` 실행'})")


def _emit(args, obj, text):
    print(json.dumps(obj, ensure_ascii=False, indent=1) if getattr(args, "json", False) else text)


def _fmt_rows(rows):
    out = [f"{'ROLE':<14}{'PANE':<9}{'STATE':<10}{'AGENT':<9}"]
    for r in rows:
        name = r["role"] + ("" if r["instance"] == 1 else f"#{r['instance']}") + ("*" if r.get("lead") else "")
        out.append(f"{name:<14}{(r['pane'] or '-'):<9}{r['state']:<10}{(r.get('agent_status') or '-'):<9}"
                   + (f" {r['error']}" if r.get("error") else ""))
    return "\n".join(out)


# ------------------------------------------------------------------ commands
def cmd_init(args, h):
    scope, cdir = _scope(args)
    _keep_out_of_git(scope, cdir)
    copied = []
    for root, _, files in os.walk(EXAMPLES):
        for f in files:
            src = os.path.join(root, f)
            dst = os.path.join(cdir, os.path.relpath(src, EXAMPLES))
            if os.path.exists(dst) and not args.force:
                continue
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copyfile(src, dst)
            copied.append(dst)
    print(f"config dir: {cdir}")
    print("copied:" if copied else "nothing to copy (use --force to overwrite)")
    for c in copied:
        print("  " + c)


def cmd_assign(args, h):
    roles = config.load_roles()
    pane, ws = _where(h, args)
    teammod.assign(h, Store(), roles, ws, pane, args.role)
    print(f"{pane} → {args.role}")


def cmd_unassign(args, h):
    pane, ws = _where(h, args)
    print("removed" if teammod.unassign(h, Store(), ws, pane) else "not assigned")


def _has_terminal():
    return sys.stdin.isatty() and sys.stdout.isatty()


def _project_target():
    """`--project`: the `.herdr-roles/` dir of the project around the current directory (created on write)."""
    return os.path.join(config.project_root(os.getcwd()), config.PROJECT_DIRNAME)


SCOPE_LABELS = {"global": "전역", "project": "프로젝트", "user": "사용자"}


def _scope(args):
    """`--scope` (or the older `--project`) -> (scope, the dir it writes to)."""
    scope = args.scope or ("project" if args.project else "global")
    if args.project and scope != "project":
        raise CliError("--project 는 --scope project 와 같습니다. 둘 중 하나만 쓰세요.")
    if scope == "global":
        return scope, config.config_dir()
    project = _project_target()
    return scope, project if scope == "project" else config.local_dir(project)


def _keep_out_of_git(scope, cdir):
    """The user layer lives inside the project's `.herdr-roles/`: a `*` .gitignore keeps it out of commits."""
    if scope != "user":
        return
    os.makedirs(cdir, exist_ok=True)
    path = os.path.join(cdir, ".gitignore")
    if not os.path.exists(path):
        with open(path, "w") as f:
            f.write("# herdr-roles 사용자 설정: 이 사용자만 씁니다 (커밋하지 않음)\n*\n")


def _run_wizard(preset=None, cdir=None):
    try:
        team = setupmod.run_wizard(preset=preset, cdir=cdir)
    except SetupAborted as e:
        raise CliError(str(e))
    except EOFError:    # its own message ("EOF when reading a line") means nothing to the user
        raise CliError("입력이 끝나 설정을 중단했습니다. 질문에 답할 수 있는 터미널에서 실행하거나, "
                       "추천값으로 진행하려면 `yes '' | roles setup --preset <프리셋>` 처럼 실행하세요.")
    # every wizard path (setup, team-up, picker) ends here: make `roles` reachable from agent shells too
    print("\n에이전트에서 쓸 수 있게 연결:")
    print("\n".join(installmod.install()))
    return team


def cmd_config(args, h):
    """Show every config layer that applies here and what each one defines (later layers win)."""
    rows = []
    for scope, d in config.scoped_layers():
        row = {"scope": scope, "dir": d, "exists": os.path.isdir(d), "roles": [], "teams": [], "workflows": [],
               "settings": {}}
        if row["exists"]:
            try:
                path = os.path.join(d, "roles.toml")
                row["roles"] = sorted(config._load(path).get("roles", {})) if os.path.exists(path) else []
                path = os.path.join(d, "config.toml")
                row["settings"] = config._load(path).get("settings", {}) if os.path.exists(path) else {}
            except ConfigError as e:
                row["error"] = str(e)
            for sub in ("teams", "workflows"):
                sd = os.path.join(d, sub)
                row[sub] = sorted(f[:-5] for f in os.listdir(sd) if f.endswith(".toml")) if os.path.isdir(sd) else []
        rows.append(row)
    try:
        effective = config.load_settings()
    except ConfigError as e:
        effective = {"error": str(e)}
    out = []
    for r in rows:
        out.append(f"[{SCOPE_LABELS[r['scope']]}] {r['dir']}" + ("" if r["exists"] else "  (없음)"))
        if r.get("error"):
            out.append(f"  ! {r['error']}")
        for key, label in (("roles", "역할"), ("teams", "팀"), ("workflows", "워크플로우")):
            if r[key]:
                out.append(f"  {label}: {', '.join(r[key])}")
        if r["settings"]:
            out.append("  설정: " + ", ".join(f"{k}={v}" for k, v in r["settings"].items()))
    if not config.project_dir():
        out.append(f"(프로젝트 설정 없음: `roles setup --scope project|user` 를 여기서 실행하면 {_project_target()}"
                   " 에 만듭니다)")
    out.append("\n적용 결과 (아래 계층이 이김): default_team=" + str(effective.get("default_team")))
    _emit(args, {"layers": rows, "effective": effective}, "\n".join(out))


def cmd_install(args, h):
    print("\n".join(installmod.install(force=args.force)))


def _ensure_roles(h, pane):
    """No roles configured yet: interview the user (or open the setup popup when there is no terminal to ask in).

    Returns the name of the team the wizard just wrote, or None when roles already existed.
    """
    if not setupmod.needs_setup():
        return None
    if _has_terminal():
        return _run_wizard()
    # herdr action / agent shell: stdin is not a terminal, so hand the questions to a popup the user can see
    try:
        h.plugin_pane_open(PLUGIN_ID, "setup", target_pane=pane)
    except HerdrError:
        raise CliError("설정된 역할이 없습니다. 터미널에서 `roles setup` 을 실행해 역할을 정한 뒤 team-up 을 다시 실행하세요.")
    raise CliError("설정된 역할이 없어 설정 창을 열었어요. 질문에 답한 뒤 team-up 을 다시 실행하세요.")


def cmd_setup(args, h):
    if args.list_presets:
        for pid, p in setupmod.PRESETS.items():
            roles = ", ".join(f"{r['name']}({r['agent']})" for r in p["roles"])
            print(f"{pid:<18} {p['label']} — 리드 {p['lead']}, 역할 {roles}\n{'':<18} {p['description']}")
        return
    if args.preset and args.preset not in setupmod.PRESETS:
        raise CliError(f"알 수 없는 프리셋 '{args.preset}' (사용 가능: {', '.join(setupmod.PRESETS)})")
    scope, cdir = _scope(args)
    if not setupmod.needs_setup(cdir) and not args.force:
        raise CliError(f"이미 역할이 설정돼 있습니다: {cdir} (다시 정하려면 --force: roles.toml 을 덮어씁니다).")
    _keep_out_of_git(scope, cdir)
    team = _run_wizard(args.preset, cdir)
    root = os.path.dirname(cdir if scope == "project" else os.path.dirname(cdir))
    where = f"{SCOPE_LABELS[scope]} 설정으로 저장했습니다. 이 프로젝트({root}) 안의 " if scope != "global" else ""
    print(f"\n팀 '{team}' 준비 완료. {where}리드로 쓸 pane 에서 `roles team-up` 을 실행하세요.")
    if _has_terminal():
        try:
            input("Enter 로 닫기")
        except EOFError:
            pass


def cmd_team_up(args, h):
    pane, ws = _where(h, args)
    before = Store().load(ws)
    if before.get("team") and before.get("project") != config.project_dir():
        print(f"참고: 이 workspace 의 팀은 {before['project'] or '전역'} 설정으로 만들어졌는데, "
              f"지금은 {config.project_dir() or '전역'} 설정으로 구성합니다.")
    created = _ensure_roles(h, pane)
    roles, settings = config.load_roles(), config.load_settings()
    team = config.load_team(created or _pick_team_name(args, settings), roles)
    rep =teammod.team_up(h, Store(), roles, team, pane, settings, dry_run=args.dry_run)
    if args.json:
        print(json.dumps(rep, ensure_ascii=False, indent=1))
        return
    head = "[dry-run] " if args.dry_run else ""
    print(f"{head}team '{team.name}' (lead {rep['lead']}, workspace {rep['workspace']})")
    for p in rep["plan"] if args.dry_run else rep["spawned"]:
        print("  - " + ", ".join(f"{k}={v}" for k, v in p.items()))
    for f in rep["failed"]:
        print(f"  ! {f['role']}: {f['error']}")
    if rep["roster_state"] == "shell" and rep["roster"]:
        print("\n(리드가 에이전트가 아니어서 로스터를 주입하지 않았습니다)\n" + rep["roster"])
    elif rep["roster_state"]:
        print(f"roster: {rep['roster_state']}")


def cmd_team_down(args, h):
    _, ws = _where(h, args)
    r = teammod.team_down(h, Store(), ws)
    _emit(args, r, f"closed {len(r['closed'])} spawned pane(s): {', '.join(r['closed']) or '-'}; kept {', '.join(r['kept']) or '-'}")


def cmd_status(args, h):
    roles = config.load_roles()
    _, ws = _where(h, args)
    store = Store()
    data = store.load(ws)
    team = None
    if data.get("team"):
        try:
            team = config.load_team(data["team"], roles)
        except ConfigError:
            pass
    st = teammod.team_status(h, store, roles, ws, team)
    run = st["run"]
    text = f"workspace {ws}  team: {st['team'] or '-'}\n" + _fmt_rows(st["rows"])
    if run:
        text += (f"\n\nworkflow {run['workflow']}: {run['state']}"
                 + (f" ({run['reason']})" if run.get("reason") else "")
                 + f"  hops={run['hops']}"
                 + (f"  now={run['current']['step']}@{run['current']['pane']}" if run.get("current") else "")
                 + (f"  pending={run['pending']['step']}" if run.get("pending") else ""))
    if args.notify:
        h.notify("Roles 상태", text[:400])
    _emit(args, st, text)


def _find_role_pane(h, store, ws, role, roles_cfg=None):
    live = {p["pane_id"]: p for p in h.pane_list(ws)}
    pane = wfmod.choose_pane(store.load(ws), role, live)
    if not pane:
        raise CliError(f"역할 '{role}'의 pane이 없습니다 (먼저 team-up 또는 assign).")
    return pane


def _one_target(args):
    if bool(args.role) == bool(args.fleet):
        raise CliError("--role(이 팀의 역할) 과 --fleet(fleet 팀 이름) 중 하나만 지정하세요.")


def cmd_send(args, h):
    _one_target(args)
    if args.fleet:
        sender, _ = _where(h, args)
        text = sys.stdin.read() if args.text == "-" else args.text
        how = fleetmod.send(h, Store(), sender, args.fleet, text)
        print(f"{'queued' if how == 'queued' else 'sent'} to team '{args.fleet}' lead"
              + (": 리드가 지금 하던 일을 마치면 전달합니다" if how == "queued" else "")
              + "; 팀의 일이 끝나면 리드의 HANDOFF 보고가 이 pane 으로 옵니다")
        return
    roles = config.load_roles()
    if args.role not in roles:
        raise CliError(f"알 수 없는 역할 '{args.role}' (사용 가능: {', '.join(sorted(roles))})")
    sender, ws = _where(h, args)
    text = sys.stdin.read() if args.text == "-" else args.text
    store = Store()
    pane = _find_role_pane(h, store, ws, args.role)
    if not roles[args.role].agent:
        wfmod.deliver(h, pane, roles[args.role], text)
        print(f"sent to {args.role} ({pane})")
        return
    live = {p["pane_id"]: p for p in h.pane_list(ws)}
    with store.lock():
        data = store.load(ws)
        how = mail.send(h, data, roles, sender, pane, text, live)
        store.save(ws, data)
    if how == "queued":
        print(f"queued for {args.role} ({pane}): 지금 하던 일이 끝나면 전달하고, 그 결과를 이 pane 으로 보고합니다")
    else:
        print(f"sent to {args.role} ({pane})" + ("; 끝나면 결과가 이 pane 으로 보고됩니다" if how else ""))


def cmd_report(args, h):
    pane, ws = _where(h, args)
    text = sys.stdin.read() if args.text in (None, "-") else args.text
    roles, settings = config.load_roles(), config.load_settings()
    try:
        to, path = mail.report(h, Store(), roles, ws, pane, text, settings)
    except ValueError as e:
        raise CliError(str(e))
    print(f"보고했습니다 → {', '.join(to)}" if to else "보고를 받을 사람이 없습니다 (요청한 사람도, 리드도 없음)")
    print(f"전문: {path}")


def cmd_read(args, h):
    _one_target(args)
    me, ws = _where(h, args)
    pane = fleetmod.lead_pane(h, Store(), me, args.fleet) if args.fleet else _find_role_pane(h, Store(), ws, args.role)
    print(handoff.last_output(h, pane, args.lines) if args.marker else h.pane_read(pane, lines=args.lines))


def cmd_fleet_up(args, h):
    if setupmod.needs_setup():
        raise CliError("설정된 역할이 없습니다. 터미널에서 `roles setup` 으로 역할과 팀을 정한 뒤 다시 실행하세요.")
    specs = [fleetmod.parse_spec(s) for s in args.names]
    if len({n for n, _ in specs}) != len(specs):
        raise CliError("같은 팀 이름이 두 번 있습니다.")
    tasks = args.task or []
    if tasks and len(tasks) != len(specs):
        raise CliError(f"--task 는 팀마다 하나씩, 팀 이름과 같은 순서로 주세요 (팀 {len(specs)}개, --task {len(tasks)}개).")
    if "-" in tasks:
        if len(tasks) != 1:
            raise CliError("--task - (stdin) 은 팀이 하나일 때만 쓸 수 있습니다.")
        tasks = [sys.stdin.read()]
    me, _ = _where(h, args)
    store, results = Store(), []
    for i, (name, team) in enumerate(specs):
        try:
            r = fleetmod.up(h, store, me, name, team or args.team, tasks[i] if tasks else None, cwd=args.cwd,
                            worktree=args.worktree)
        except (ConfigError, fleetmod.FleetError, teammod.TeamError, HerdrError) as e:
            r = {"name": name, "error": str(e)}
        results.append(r)
    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=1))
    for r in [] if args.json else results:
        if r.get("error"):
            print(f"! {r['name']}: {r['error']}", file=sys.stderr)
            continue
        verb = "launched" if r["created"] else "reconciled"
        print(f"{verb} '{r['name']}' (team {r['team']}, workspace {r['workspace']}, lead {r['lead']})"
              + (f" worktree {r['path']}" if r.get("path") else "")
              + (f"; task {r['task']}" if r["task"] else ""))
        for f in r["failed"] + [x for x in r["spawned"] if x["status"] != "ready"]:
            print(f"  ! {f['role']}: {f.get('error') or f['status']}")
    if not args.json and any(r.get("task") for r in results):
        print("\n각 팀의 일이 끝나면 리드의 HANDOFF 보고가 이 pane 으로 옵니다. 기다리며 확인할 필요는 없습니다.")
    if any(r.get("error") for r in results):
        raise CliError(f"{sum(1 for r in results if r.get('error'))}개 팀을 띄우지 못했습니다.")


def cmd_fleet(args, h):
    me, _ = _where(h, args)
    st = fleetmod.status(h, Store(), me)
    out = [f"{'TEAM':<16}{'CONFIG':<10}{'WS':<6}{'LEAD':<9}{'STATE':<10}{'MEMBERS':<9}{'REPORTS':<8}"]
    for r in st["teams"]:
        out.append(f"{r['name']:<16}{r['team']:<10}{r['workspace']:<6}{(r['lead_status'] or '-'):<9}{r['state']:<10}"
                   f"{r['members_working']}/{r['members']:<7}{r['reports']:<8}"
                   + (f" task: {r['task']}" if r.get("task") else "")
                   + (f"\n{'':<16}↳ {r['last_report']}" if r.get("last_report") else ""))
    _emit(args, st, "\n".join(out) if st["teams"] else "fleet 팀이 없습니다 (`roles fleet-up <이름> --task ...`).")


def cmd_fleet_down(args, h):
    me, _ = _where(h, args)
    closed = fleetmod.down(h, Store(), me, args.names)
    text = "\n".join(f"closed '{c['name']}' ({c['workspace']})" + (f"; worktree 는 남겨 둠: {c['path']}" if c["path"] else "")
                     for c in closed) or "닫을 fleet 팀이 없습니다."
    _emit(args, closed, text)


def cmd_run(args, h):
    roles, settings = config.load_roles(), config.load_settings()
    _, ws = _where(h, args)
    store = Store()
    name = args.workflow or settings["default_workflow"]
    if not name:
        data = store.load(ws)
        if data.get("team"):
            name = config.load_team(data["team"], roles).workflow
    if not name:
        raise CliError("--workflow 가 필요합니다 (팀/설정에 기본 워크플로우가 없습니다).")
    wf = config.load_workflow(name, roles)
    text = sys.stdin.read() if args.input == "-" else (args.input or "")
    run = wfmod.start(h, store, roles, wf, ws, text, force=args.force)
    print(f"workflow '{wf.name}' started at step '{run['current']['step']}' ({run['current']['pane']})")


def cmd_advance(args, h):
    roles = config.load_roles()
    _, ws = _where(h, args)
    print(f"handoff delivered to step '{wfmod.advance(h, Store(), roles, ws)}'")


def cmd_stop(args, h):
    _, ws = _where(h, args)
    print("stopped" if wfmod.stop(Store(), ws) else "no workflow")


def cmd_reapply(args, h):
    """Startup hook: herdr drops pane metadata on restart, so re-project it from state and prune dead panes."""
    store = Store()
    if not store.workspaces():
        return
    live_by_ws = {}
    for p in h.pane_list():
        live_by_ws.setdefault(p["workspace_id"], set()).add(p["pane_id"])
    for ws in store.workspaces():
        with store.lock():
            data = store.load(ws)
            config.set_project(data["project"])     # each workspace's team may come from a different project
            try:
                roles = config.load_roles()
            except ConfigError as e:
                print(f"{ws}: skipped ({e})")
                continue
            live = live_by_ws.get(ws, set())
            data["panes"] = {pid: p for pid, p in data["panes"].items() if pid in live}
            bad = display.apply_all(h, data, roles)
            store.save(ws, data)
        if not args.quiet:
            print(f"{ws}: {len(data['panes'])} pane(s) re-projected" + (f", {len(bad)} failed" if bad else ""))


def cmd_dispatch(args, h):
    """Event hook entry point. Cheap no-op for workspaces we do not track."""
    ev = os.environ.get("HERDR_PLUGIN_EVENT", "")
    try:
        payload = json.loads(os.environ.get("HERDR_PLUGIN_EVENT_JSON") or "{}").get("data", {})
    except json.JSONDecodeError:
        return
    ws, pane = payload.get("workspace_id"), payload.get("pane_id")
    store = Store()
    if not ws or not pane or not store.exists(ws):
        return
    config.set_project(store.load(ws)["project"])    # hooks run without the team's cwd
    roles, settings = config.load_roles(), config.load_settings()
    status = payload.get("agent_status")
    try:
        if ev == "pane.agent_status_changed":
            teammod.flush_roster(h, store, ws, pane, status)
            res = ",".join(filter(None, [mail.on_status(h, store, roles, ws, pane, status, settings),
                                         wfmod.on_status(h, store, roles, ws, pane, status, settings)]))
        elif ev == "pane.closed":
            res = ",".join(filter(None, [mail.on_pane_closed(store, ws, pane),
                                         wfmod.on_pane_closed(h, store, ws, pane)]))
        else:
            return
    except LockTimeout as e:
        print(f"skipped {ev} {pane}: {e}")
        _debug(f"{ws} {ev} {pane} {status}: lock timeout")
        return
    _debug(f"{ws} {ev} {pane} {status}: {res}")
    if res:
        print(f"{ev} {pane}: {res}")


def _debug(line):
    """ROLES_DEBUG=1 appends one line per handled event to <state dir>/dispatch.log (hooks are otherwise silent)."""
    if not os.environ.get("ROLES_DEBUG"):
        return
    try:
        os.makedirs(config.state_dir(), exist_ok=True)
        with open(os.path.join(config.state_dir(), "dispatch.log"), "a") as f:
            f.write(f"{time.strftime('%H:%M:%S')} {line}\n")
    except OSError:
        pass


def cmd_open_picker(args, h):
    h.plugin_pane_open(PLUGIN_ID, "picker", target_pane=_context_pane())


def cmd_board(args, h):
    roles = config.load_roles()
    pane, ws = _where(h, args)
    store = Store()
    while True:
        data = store.load(ws)
        team = config.load_team(data["team"], roles) if data.get("team") else None
        st = teammod.team_status(h, store, roles, ws, team)
        run = st["run"]
        lines = [f"herdr-roles  workspace {ws}  team: {st['team'] or '-'}", "", _fmt_rows(st["rows"])]
        if run:
            lines += ["", f"workflow {run['workflow']}: {run['state']} hops={run['hops']}"
                      + (f" now={run['current']['step']}@{run['current']['pane']}" if run.get("current") else "")
                      + (f" pending→{run['pending']['step']}" if run.get("pending") else "")
                      + (f"  ({run['reason']})" if run.get("reason") else "")]
        sys.stdout.write("\033[2J\033[H" + "\n".join(lines) + "\n")
        sys.stdout.flush()
        if args.once:
            return
        time.sleep(2)


def cmd_pick(args, h):
    """Interactive menu shown in the `picker` popup."""
    if setupmod.needs_setup():
        _run_wizard()
    roles = config.load_roles()
    pane = _context_pane()
    ns = lambda **kw: argparse.Namespace(pane=pane, json=False, **kw)  # noqa: E731
    menu = [("역할 지정", "assign"), ("팀 구성(team-up)", "team-up"), ("팀 해산(team-down)", "team-down"),
            ("워크플로우 실행", "run"), ("인계 전달(advance)", "advance"), ("상태 보기", "status")]
    while True:
        print(f"\nherdr-roles — 대상 pane {pane}")
        for i, (label, _) in enumerate(menu, 1):
            print(f"  {i}. {label}")
        choice = input("번호 (q=종료): ").strip().lower()
        if choice in ("q", ""):
            return
        try:
            action = menu[int(choice) - 1][1]
            if action == "assign":
                names = sorted(roles)
                for i, n in enumerate(names, 1):
                    print(f"  {i}. {n} ({roles[n].label})")
                cmd_assign(ns(role=names[int(input('역할 번호: ')) - 1]), h)
            elif action == "team-up":
                names = config.list_names("teams")
                for i, n in enumerate(names, 1):
                    print(f"  {i}. {n}")
                cmd_team_up(ns(team=names[int(input('팀 번호: ')) - 1], dry_run=False), h)
            elif action == "team-down":
                cmd_team_down(ns(), h)
            elif action == "run":
                names = config.list_names("workflows")
                for i, n in enumerate(names, 1):
                    print(f"  {i}. {n}")
                cmd_run(ns(workflow=names[int(input('워크플로우 번호: ')) - 1], input=input("요청: "), force=False), h)
            elif action == "advance":
                cmd_advance(ns(), h)
            else:
                cmd_status(ns(notify=False), h)
        except (ValueError, IndexError):
            print("잘못된 입력")
        except (HerdrError, ConfigError, CliError, teammod.TeamError, wfmod.WorkflowError) as e:
            print(f"error: {e}")


# ------------------------------------------------------------------ parser
def build_parser():
    p = argparse.ArgumentParser(prog="roles", description="herdr pane roles, teams and workflows")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add(name, fn, *, pane=True, json_out=False, **kw):
        sp = sub.add_parser(name, **kw)
        sp.set_defaults(fn=fn)
        if pane:
            sp.add_argument("--pane", help="대상 pane (기본: $HERDR_PANE_ID / 포커스 pane)")
        if json_out:
            sp.add_argument("--json", action="store_true")
        return sp

    project_help = "--scope project 와 같음"
    scope_help = ("저장할 계층: global(이 PC 전체, 기본) / project(<git 루트>/.herdr-roles/, 커밋해 공유) / "
                  "user(<git 루트>/.herdr-roles/local/, 나만 씀·git 제외). 우선순위 global < project < user")
    s = add("init", cmd_init, pane=False, help="예제 설정을 설정 디렉터리로 복사")
    s.add_argument("--force", action="store_true")
    s.add_argument("--scope", choices=tuple(SCOPE_LABELS), help=scope_help)
    s.add_argument("--project", action="store_true", help=project_help)
    s = add("setup", cmd_setup, pane=False, help="질문에 답하며 역할·팀 설정 만들기 (역할이 없으면 team-up 이 자동 실행)")
    s.add_argument("--force", action="store_true", help="이미 역할이 있어도 다시 정함 (roles.toml 덮어씀)")
    s.add_argument("--preset", help="기업 팀 구조 프리셋으로 바로 시작 (목록: --list-presets)")
    s.add_argument("--list-presets", action="store_true", help="사용 가능한 팀 구조 프리셋 보기")
    s.add_argument("--scope", choices=tuple(SCOPE_LABELS), help=scope_help)
    s.add_argument("--project", action="store_true", help=project_help)
    add("config", cmd_config, pane=False, json_out=True, help="전역·프로젝트·사용자 설정 계층과 각자 정의한 것 보기")
    s = add("install", cmd_install, pane=False,
            help="`roles` 명령을 ~/.local/bin 에, 에이전트 스킬을 Claude Code·Codex 에 연결 (setup 이 자동 실행)")
    s.add_argument("--force", action="store_true", help="다른 곳을 가리키는 기존 링크도 이 플러그인으로 바꿈")
    s = add("assign", cmd_assign, help="pane에 역할 지정")
    s.add_argument("--role", required=True)
    add("unassign", cmd_unassign, help="pane 역할 해제")
    s = add("team-up", cmd_team_up, json_out=True, help="이 pane을 lead로 팀을 구성 (부족한 pane만 스폰)")
    s.add_argument("--team")
    s.add_argument("--dry-run", action="store_true")
    add("team-down", cmd_team_down, json_out=True, help="스폰한 pane만 정리")
    s = add("status", cmd_status, json_out=True, help="팀/워크플로우 상태")
    s.add_argument("--notify", action="store_true", help="알림으로도 표시")
    s = add("send", cmd_send, help="역할 이름(또는 fleet 팀 이름)으로 프롬프트/명령 전송")
    s.add_argument("--role")
    s.add_argument("--fleet", metavar="팀이름", help="fleet 팀의 리드에게 요청 (보고는 이 pane 으로)")
    s.add_argument("--text", required=True, help="'-' 이면 stdin")
    s = add("report", cmd_report, help="맡은 일의 결과를 보고 (요청한 쪽에, 없으면 리드·오케스트레이터에게). 전문은 파일로 남김")
    s.add_argument("--text", help="보고 내용 (생략하거나 '-' 이면 stdin)")
    s = add("read", cmd_read, help="역할 pane(또는 fleet 팀 리드)의 최근 출력 읽기")
    s.add_argument("--role")
    s.add_argument("--fleet", metavar="팀이름")
    s.add_argument("--lines", type=int, default=200)
    s.add_argument("--marker", action="store_true", help="<<<HANDOFF ... >>> 블록만 추출")
    s = add("fleet-up", cmd_fleet_up, json_out=True,
            help="이 pane 을 오케스트레이터로, 팀마다 새 workspace 를 열어 팀을 띄우고 일을 맡김 (팀끼리 병렬 작업)")
    s.add_argument("names", nargs="+", metavar="이름[=팀설정]", help="fleet 팀 이름 (=팀설정 으로 팀 구성 지정)")
    s.add_argument("--team", help="팀 설정 기본값 (기본: default_team)")
    s.add_argument("--task", action="append", metavar="지시", help="팀마다 하나씩, 이름 순서대로 ('-' 이면 stdin, 팀 하나일 때)")
    s.add_argument("--cwd", help="팀의 작업 폴더 (기본: 이 pane 의 폴더)")
    s.add_argument("--worktree", action="store_true", help="팀마다 git worktree(브랜치 roles/<이름>)를 만들어 그 안에서 작업")
    add("fleet", cmd_fleet, json_out=True, help="fleet 팀 상태")
    s = add("fleet-down", cmd_fleet_down, json_out=True, help="fleet 팀의 workspace 를 닫음 (이름 없으면 전부)")
    s.add_argument("names", nargs="*", metavar="이름")
    s = add("run", cmd_run, help="워크플로우 시작")
    s.add_argument("--workflow")
    s.add_argument("--input", default="", help="'-' 이면 stdin")
    s.add_argument("--force", action="store_true")
    add("advance", cmd_advance, help="대기 중인 인계를 전달")
    add("stop", cmd_stop, help="워크플로우 중단")
    s = add("reapply", cmd_reapply, pane=False, help="표시 메타데이터 재적용 (startup 훅)")
    s.add_argument("--quiet", action="store_true")
    add("dispatch", cmd_dispatch, pane=False, help="이벤트 훅 진입점")
    add("open-picker", cmd_open_picker, help="picker 팝업 열기")
    add("pick", cmd_pick, pane=False, help="대화형 메뉴 (picker pane)")
    s = add("board", cmd_board, help="상태 대시보드 (board pane)")
    s.add_argument("--once", action="store_true")
    return p


def _choose_project(args, h):
    """Pick the project layer for this command.

    A workspace that already has a team keeps the project it was built from (send/read/status/run act on that team);
    otherwise the target pane's cwd decides, then the shell's. Hooks (dispatch/reapply) read it per workspace instead.
    """
    if args.cmd in ("dispatch", "reapply"):
        config.set_project(None)
        return
    info = None
    pane = getattr(args, "pane", None) or _context_pane()
    if pane and args.cmd not in ("init", "setup", "install"):
        try:
            info = h.pane_get(pane)
        except HerdrError:
            info = None
    if info and args.cmd != "team-up":
        store = Store()
        ws = info.get("workspace_id")
        if ws and store.exists(ws):
            data = store.load(ws)
            if data.get("team"):
                config.set_project(data["project"])
                return
    cwd = (info or {}).get("cwd") or (info or {}).get("foreground_cwd") or os.getcwd()
    config.set_project(config.find_project(cwd))


def main(argv=None, h=None):
    args = build_parser().parse_args(argv)
    h = h or Herdr()
    try:
        _choose_project(args, h)
        args.fn(args, h)
        return 0
    except (ConfigError, CliError, teammod.TeamError, wfmod.WorkflowError, HerdrError, LockTimeout,
            fleetmod.FleetError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
