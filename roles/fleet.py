"""Fleet: one orchestrator pane runs several teams in parallel, each in its own herdr workspace.

`up` opens a workspace per team (optionally on its own git worktree), makes its root pane the lead, starts the
lead's agent and the rest of the team (team.team_up) and hands the lead its first request. The orchestrator's
workspace state keeps the fleet (`fleet`: name -> workspace, lead, ...); each team's keeps `parent`. Requests from
the orchestrator are sticky asks (mail.py), so the lead's final HANDOFF block is reported back to the orchestrator.

Teams are started one after another (the state lock is held while a team spawns); they then work in parallel.
"""
import os
import re

from . import config, handoff, mail, team as teammod
from .herdr import HerdrError
from .state import now_ms

NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,39}$")
ORCH = mail.ORCHESTRATOR


class FleetError(RuntimeError):
    pass


def lead_brief(name, roles, team):
    """What a fleet team's lead is told on top of the usual roster: who it answers to and when to report."""
    lead = roles[team.lead.role]
    lines = [f"[herdr-roles] 이 팀은 {ORCH}가 병렬로 띄운 팀 '{name}'입니다. 당신은 리드로서 {ORCH}의 요청을 받아 "
             "팀원에게 나눠 맡기고 결과를 모읍니다.",
             f"- {ORCH}의 요청은 \"{mail.TAG} {ORCH}의 요청\" 머리말로 옵니다.",
             "- 팀원에게 일을 넘기고 턴을 마칠 때는 HANDOFF 블록을 쓰지 마세요. 팀원의 보고는 끝나는 대로 이 pane 으로 옵니다.",
             "- 요청한 일이 모두 끝나면 마지막에 <<<HANDOFF ... >>> 블록으로 결과(한 일, 바뀐 파일, 남은 문제)를 요약하세요. "
             f"그 블록이 {ORCH}에게 보고됩니다.",
             f"- {ORCH}의 결정이 필요하면 질문을 HANDOFF 블록으로 남기세요. 답은 새 요청으로 옵니다."]
    if lead.prompt:
        lines += ["", lead.prompt]
    return "\n".join(lines)


def parse_spec(spec):
    """`auth` or `auth=dev` (fleet team name, optionally the team config to build it from)."""
    name, _, team = spec.partition("=")
    if not NAME.match(name):
        raise FleetError(f"팀 이름 '{name}' 은(는) 영문·숫자·._- 로 40자 이내여야 합니다.")
    return name, team or None


def _orchestrator(h, pane):
    info = h.pane_get(pane)
    if not info:
        raise FleetError(f"pane {pane} 을(를) 찾을 수 없습니다.")
    return info


def _entry(store, ows, name):
    fleet = store.load(ows)["fleet"]
    if name not in fleet:
        raise FleetError(f"팀 '{name}' 이(가) 없습니다 (있는 팀: {', '.join(sorted(fleet)) or '없음 — fleet-up 으로 띄우세요'}).")
    return fleet[name]


def _pick_team(team_name, settings):
    name = team_name or settings["default_team"]
    if name:
        return name
    names = config.list_names("teams")
    if len(names) == 1:
        return names[0]
    raise FleetError(f"팀 설정을 정하세요: <이름>=<팀> 또는 --team (사용 가능: {', '.join(names) or '없음'})")


def up(h, store, orch_pane, name, team_name=None, task=None, cwd=None, worktree=False):
    """Start (or reconcile) the fleet team `name` and give its lead `task`. Re-running for a live team only fills
    in what is missing, like team-up, and sends `task` as a new request."""
    info = _orchestrator(h, orch_pane)
    ows = info["workspace_id"]
    base = cwd or info.get("cwd") or info.get("foreground_cwd") or os.getcwd()
    with store.lock():
        entry = store.load(ows)["fleet"].get(name)
    panes = {p["pane_id"]: p for p in h.pane_list()}
    created = not (entry and any(p["workspace_id"] == entry["workspace"] for p in panes.values()))
    project = entry["project"] if entry and not created else config.find_project(base)
    config.set_project(project)
    roles, settings = config.load_roles(), config.load_settings()
    team = config.load_team(_pick_team(team_name or (entry or {}).get("team"), settings), roles)
    if not roles[team.lead.role].agent:
        raise FleetError(f"팀 '{team.name}' 의 리드 역할 '{team.lead.role}' 에 에이전트가 없어 요청을 받을 수 없습니다.")

    path = None
    if created:
        try:
            if worktree:
                tws, lead, path = h.worktree_create(base, f"roles/{name}", name)
            else:
                tws, lead = h.workspace_create(base, name)
        except HerdrError as e:
            raise FleetError(f"팀 '{name}' 의 workspace 를 만들지 못했습니다: {e}")
    else:
        tws, lead, path = entry["workspace"], entry["lead"], entry.get("path")
    # a lead without a running agent (new, or lost to a herdr restart) is briefed and handed `task` in one prompt
    fresh = created or not (panes.get(lead) or {}).get("agent")
    origin = {"pane": orch_pane, "workspace": ows}

    with store.lock():
        tdata = store.load(tws)
        tdata["parent"] = {"workspace": ows, "pane": orch_pane, "name": name}
        if fresh and task:     # the request rides in the roster prompt; track it from the start
            # a reused pane may still show a block from before: only a newer one answers this request
            baseline = None if created else handoff.last_block(h, lead, settings["handoff_lines"])
            mail.track_up(tdata, lead, origin, task, queued=False, baseline=baseline)
        store.save(tws, tdata)
        odata = store.load(ows)
        old = odata["fleet"].get(name) or {}
        odata["fleet"][name] = {"workspace": tws, "lead": lead, "team": team.name, "project": project,
                                "path": path, "task": mail.first_line(task) if task else old.get("task"),
                                "created_at": old.get("created_at") if not created else now_ms(),
                                "reports": 0 if created else old.get("reports", 0),
                                "last_report": None if created else old.get("last_report")}
        if odata["project"] is None:    # the orchestrator's own hooks load roles from here
            odata["project"] = project
        store.save(ows, odata)

    brief = None
    if fresh:
        brief = lead_brief(name, roles, team) + (f"\n\n{mail.orchestrator_request(task)}" if task else "")
    try:
        rep = teammod.team_up(h, store, roles, team, lead, settings, start_lead=True, brief=brief)
    except teammod.TeamError:
        if created:
            _close(h, store, ows, name, tws)
        raise

    sent = "sent" if fresh and task else None
    if task and not fresh:
        live = {p["pane_id"]: p for p in h.pane_list(tws)}
        with store.lock():
            tdata = store.load(tws)
            sent = mail.send(h, tdata, roles, orch_pane, lead, task, live, origin=origin,
                             lines=settings["handoff_lines"])
            store.save(tws, tdata)
    tdata = store.load(tws)
    spawned = [{**x, "error": (tdata["panes"].get(x["pane"]) or {}).get("error")} for x in rep["spawned"]]
    return {"name": name, "team": team.name, "workspace": tws, "lead": lead, "path": path, "created": created,
            "task": sent, "spawned": spawned, "failed": rep["failed"]}


def send(h, store, orch_pane, name, text):
    """A new request from the orchestrator to the lead of fleet team `name` (reported back like the first)."""
    ows = _orchestrator(h, orch_pane)["workspace_id"]
    entry = _entry(store, ows, name)
    config.set_project(entry["project"])
    roles, settings = config.load_roles(), config.load_settings()
    live = {p["pane_id"]: p for p in h.pane_list(entry["workspace"])}
    if entry["lead"] not in live:
        raise FleetError(f"팀 '{name}' 의 리드 pane 이 없습니다. `fleet-up {name}` 으로 다시 띄우세요.")
    with store.lock():
        tdata = store.load(entry["workspace"])
        how = mail.send(h, tdata, roles, orch_pane, entry["lead"], text, live,
                        origin={"pane": orch_pane, "workspace": ows}, lines=settings["handoff_lines"])
        store.save(entry["workspace"], tdata)
        odata = store.load(ows)
        odata["fleet"][name]["task"] = mail.first_line(text)
        store.save(ows, odata)
    return how


def lead_pane(h, store, orch_pane, name):
    return _entry(store, _orchestrator(h, orch_pane)["workspace_id"], name)["lead"]


def status(h, store, orch_pane):
    ows = _orchestrator(h, orch_pane)["workspace_id"]
    fleet = store.load(ows)["fleet"]
    by_ws = {}
    for p in h.pane_list():
        by_ws.setdefault(p["workspace_id"], {})[p["pane_id"]] = p
    rows = []
    for name, e in sorted(fleet.items()):
        live = by_ws.get(e["workspace"], {})
        tdata = store.load(e["workspace"])
        members = [pid for pid, p in tdata["panes"].items() if p.get("team") and pid != e["lead"]]
        lead = live.get(e["lead"])
        waiting = any(a.get("sticky") for a in (tdata["asks"].get(e["lead"]) or {}).get("from", []))
        if not lead:
            state = "gone"
        elif waiting:
            state = "working"     # a request is out and its HANDOFF has not come back yet
        else:
            state = "reported" if e.get("reports") else "idle"
        rows.append({"name": name, "team": e["team"], "workspace": e["workspace"], "lead": e["lead"], "state": state,
                     "lead_status": (lead or {}).get("agent_status"),
                     "members": len(members),
                     "members_working": sum(1 for pid in members if (live.get(pid) or {}).get("agent_status") == "working"),
                     "reports": e.get("reports", 0), "task": e.get("task"), "last_report": e.get("last_report"),
                     "path": e.get("path")})
    return {"workspace": ows, "teams": rows}


def _close(h, store, ows, name, tws):
    teammod.team_down(h, store, tws)
    try:
        h.workspace_close(tws)     # also closes the lead: its pane was made for this team
    except HerdrError:
        pass
    store.remove(tws)              # `workspace close` may not fire pane.closed hooks: don't wait for them
    with store.lock():
        odata = store.load(ows)
        entry = odata["fleet"].pop(name, None)
        store.save(ows, odata)
    return entry


def down(h, store, orch_pane, names=None):
    """Close fleet teams (all when `names` is empty). Worktree checkouts are left on disk."""
    ows = _orchestrator(h, orch_pane)["workspace_id"]
    fleet = store.load(ows)["fleet"]
    for n in names or []:
        _entry(store, ows, n)
    closed = []
    for name in (names or sorted(fleet)):
        entry = _close(h, store, ows, name, fleet[name]["workspace"])
        closed.append({"name": name, "workspace": fleet[name]["workspace"], "path": (entry or {}).get("path")})
    return closed
