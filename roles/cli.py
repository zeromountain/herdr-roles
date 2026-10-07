"""`bin/roles` subcommands. Also the target of every manifest command (actions, events, startup, panes)."""
import argparse
import json
import os
import shutil
import sys
import time

from . import config, display, handoff, setup as setupmod, team as teammod, workflow as wfmod
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
    cdir = config.config_dir()
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


def _run_wizard(preset=None):
    try:
        return setupmod.run_wizard(preset=preset)
    except (SetupAborted, EOFError) as e:
        raise CliError(str(e) or "입력이 끝나 설정을 중단했습니다.")


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
    if not setupmod.needs_setup() and not args.force:
        raise CliError("이미 역할이 설정돼 있습니다 (다시 정하려면 --force: roles.toml 을 덮어씁니다).")
    team = _run_wizard(args.preset)
    print(f"\n팀 '{team}' 준비 완료. 리드로 쓸 pane 에서 `roles team-up` 을 실행하세요.")
    if _has_terminal():
        try:
            input("Enter 로 닫기")
        except EOFError:
            pass


def cmd_team_up(args, h):
    pane, _ = _where(h, args)
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


def cmd_send(args, h):
    roles = config.load_roles()
    if args.role not in roles:
        raise CliError(f"알 수 없는 역할 '{args.role}' (사용 가능: {', '.join(sorted(roles))})")
    _, ws = _where(h, args)
    text = sys.stdin.read() if args.text == "-" else args.text
    pane = _find_role_pane(h, Store(), ws, args.role)
    wfmod.deliver(h, pane, roles[args.role], text)
    print(f"sent to {args.role} ({pane})")


def cmd_read(args, h):
    _, ws = _where(h, args)
    pane = _find_role_pane(h, Store(), ws, args.role)
    print(handoff.last_output(h, pane, args.lines) if args.marker else h.pane_read(pane, lines=args.lines))


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
    roles = config.load_roles()
    live_by_ws = {}
    for p in h.pane_list():
        live_by_ws.setdefault(p["workspace_id"], set()).add(p["pane_id"])
    for ws in store.workspaces():
        with store.lock():
            data = store.load(ws)
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
    roles, settings = config.load_roles(), config.load_settings()
    status = payload.get("agent_status")
    try:
        if ev == "pane.agent_status_changed":
            teammod.flush_roster(h, store, ws, pane, status)
            res = wfmod.on_status(h, store, roles, ws, pane, status, settings)
        elif ev == "pane.closed":
            res = wfmod.on_pane_closed(h, store, ws, pane)
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

    s = add("init", cmd_init, pane=False, help="예제 설정을 설정 디렉터리로 복사")
    s.add_argument("--force", action="store_true")
    s = add("setup", cmd_setup, pane=False, help="질문에 답하며 역할·팀 설정 만들기 (역할이 없으면 team-up 이 자동 실행)")
    s.add_argument("--force", action="store_true", help="이미 역할이 있어도 다시 정함 (roles.toml 덮어씀)")
    s.add_argument("--preset", help="기업 팀 구조 프리셋으로 바로 시작 (목록: --list-presets)")
    s.add_argument("--list-presets", action="store_true", help="사용 가능한 팀 구조 프리셋 보기")
    s = add("assign", cmd_assign, help="pane에 역할 지정")
    s.add_argument("--role", required=True)
    add("unassign", cmd_unassign, help="pane 역할 해제")
    s = add("team-up", cmd_team_up, json_out=True, help="이 pane을 lead로 팀을 구성 (부족한 pane만 스폰)")
    s.add_argument("--team")
    s.add_argument("--dry-run", action="store_true")
    add("team-down", cmd_team_down, json_out=True, help="스폰한 pane만 정리")
    s = add("status", cmd_status, json_out=True, help="팀/워크플로우 상태")
    s.add_argument("--notify", action="store_true", help="알림으로도 표시")
    s = add("send", cmd_send, help="역할 이름으로 프롬프트/명령 전송")
    s.add_argument("--role", required=True)
    s.add_argument("--text", required=True, help="'-' 이면 stdin")
    s = add("read", cmd_read, help="역할 pane의 최근 출력 읽기")
    s.add_argument("--role", required=True)
    s.add_argument("--lines", type=int, default=200)
    s.add_argument("--marker", action="store_true", help="<<<HANDOFF ... >>> 블록만 추출")
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


def main(argv=None, h=None):
    args = build_parser().parse_args(argv)
    h = h or Herdr()
    try:
        args.fn(args, h)
        return 0
    except (ConfigError, CliError, teammod.TeamError, wfmod.WorkflowError, HerdrError, LockTimeout) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
