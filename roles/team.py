"""Team spawning: the calling (main) pane becomes the lead, every other member is spawned around it.

`team_up` is an idempotent reconcile: it only creates what is missing, retries members whose agent failed to
start, and never touches panes it did not create.
"""
import os
from dataclasses import dataclass

from . import display
from .herdr import HerdrError

SPAWNER = "team-up"
ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))


class TeamError(RuntimeError):
    pass


@dataclass
class Slot:
    role: str
    index: int
    anchor: tuple          # slot key this one is placed next to
    direction: str
    ratio: float | None
    lead: bool = False

    @property
    def key(self):
        return (self.role, self.index)

    @property
    def group(self):
        return (self.anchor, self.direction)


def expand_slots(team):
    """Members -> concrete slots (lead first). `of`/`split` are placement hints; unset ones chain to the previous member."""
    slots, counters, first_key = [], {}, {}
    prev = lead_key = None
    for m in team.members:
        counters[m.role] = counters.get(m.role, 0)
        if m.lead:
            counters[m.role] += 1
            lead_key = (m.role, counters[m.role])
            slots.append(Slot(m.role, lead_key[1], anchor=lead_key, direction="right", ratio=None, lead=True))
            first_key.setdefault(m.role, lead_key)
            prev = lead_key
            continue
        anchor = first_key[m.of] if m.of else prev
        direction = m.split or ("right" if anchor == lead_key else "down")
        for _ in range(m.count):
            counters[m.role] += 1
            key = (m.role, counters[m.role])
            slots.append(Slot(m.role, key[1], anchor=anchor, direction=direction, ratio=m.ratio))
            first_key.setdefault(m.role, key)
            prev = key
    return slots


def _suffix(i):
    return "" if i == 1 else f"-{i}"


def agent_name(team, role, instance, ws):
    return f"{team.name}-{role}{_suffix(instance)}-{ws}"


def _mapping(data, team_name):
    return {(p["role"], p.get("instance", 1)): pid for pid, p in data["panes"].items() if p.get("team") == team_name}


def _start(h, data, pane, role, instance, team, ws, settings, lead_label):
    """Start the role's agent/command in an existing pane and record the outcome in state."""
    info = data["panes"][pane]
    try:
        if role.agent:
            h.agent_start(agent_name(team, role.name, instance, ws), role.agent, pane,
                          timeout_ms=settings["spawn_timeout_ms"], agent_args=role.agent_args)
            pre = (f"[herdr-roles] 당신은 팀 '{team.name}'의 '{role.label}' 역할입니다. "
                   f"작업은 리드({lead_label})가 보내며, 결과는 이 화면에 남기세요.")
            h.agent_prompt(pane, pre + ("\n" + role.prompt if role.prompt else ""))
        elif role.command:
            h.pane_run(pane, role.command)
        info.update(status="ready", error=None)
    except HerdrError as e:
        info.update(status="failed", error=str(e)[:300])
    return info["status"]


def _describe(role, limit=300):
    """One-line roster description: the whole role prompt with whitespace collapsed, else the label."""
    if not role:
        return ""
    text = " ".join((role.prompt or "").split())
    if not text:
        return role.label
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def build_roster(team, roles, data, lead_pane):
    lines = [f"[herdr-roles] 팀 '{team.name}' 구성 완료. 당신(이 pane)은 리드 '{roles[team.lead.role].label}'입니다.", "", "팀원:"]
    for pid, p in sorted(data["panes"].items()):
        if p.get("team") != team.name or pid == lead_pane:
            continue
        r = roles.get(p["role"])
        state = "" if p.get("status") == "ready" else f"  ⚠ {p.get('status')}: {p.get('error') or ''}"
        desc = _describe(r)
        lines.append(f"- {p['role']}{_suffix(p.get('instance', 1))} ({pid}) {desc}{state}")
    cli = f"python3 {os.path.join(ROOT, 'bin', 'roles')}"
    lines += ["", "사용법 (셸에서 실행):",
              f'- 작업 전달: {cli} send --role <역할> --text "<지시>"',
              f"- 결과 읽기: {cli} read --role <역할> --lines 200",
              f"- 팀 상태:   {cli} status",
              f"- 워크플로우: {cli} run --workflow <이름> --input \"<요청>\""]
    return "\n".join(lines)


def _deliver_roster(h, data, lead_info, lead_pane, text):
    """Inject the roster into the lead; defer while the lead is mid-task so we don't pollute its conversation."""
    if not lead_info.get("agent"):
        return "shell"
    if lead_info.get("agent_status") == "working":
        data["pending_roster"] = {"pane": lead_pane, "text": text}
        h.notify("Roles: 팀 로스터 대기", "리드가 작업 중입니다. idle이 되면 전달합니다.")
        return "deferred"
    try:
        h.agent_prompt(lead_pane, text)
        data["pending_roster"] = None
        return "sent"
    except HerdrError:
        data["pending_roster"] = {"pane": lead_pane, "text": text}
        return "deferred"


def flush_roster(h, store, ws, pane_id, status):
    if status not in ("idle", "done") or not store.exists(ws):
        return False
    with store.lock(timeout=20):
        data = store.load(ws)
        pr = data.get("pending_roster")
        if not pr or pr.get("pane") != pane_id:
            return False
        try:
            h.agent_prompt(pane_id, pr["text"])
        except HerdrError:
            return False
        data["pending_roster"] = None
        store.save(ws, data)
        return True


def team_up(h, store, roles, team, lead_pane, settings, dry_run=False):
    panes = h.pane_list()
    by_id = {p["pane_id"]: p for p in panes}
    lead_info = by_id.get(lead_pane)
    if not lead_info:
        raise TeamError(f"lead pane {lead_pane} not found in this herdr session")
    ws = lead_info["workspace_id"]
    ws_live = {pid for pid, p in by_id.items() if p["workspace_id"] == ws}
    slots = expand_slots(team)
    lead_slot = slots[0]

    with store.lock():
        data = store.load(ws)
        data["panes"] = {pid: p for pid, p in data["panes"].items() if pid in ws_live}
        mapping = _mapping(data, team.name)
        mapping[lead_slot.key] = lead_pane
        todo = [s for s in slots[1:] if s.key not in mapping]
        # retry members that failed to start, and agent members whose agent is gone (a herdr restart restores
        # the panes but not the agent processes inside them)
        retry = [s for s in slots[1:] if s.key in mapping and (
            data["panes"][mapping[s.key]].get("status") != "ready"
            or (roles[s.role].agent and not by_id[mapping[s.key]].get("agent")))]
        if len(todo) > settings["max_spawn"]:
            raise TeamError(f"team '{team.name}' needs {len(todo)} new panes; max_spawn is {settings['max_spawn']}")
        plan = ([{"role": s.role, "instance": s.index, "action": "spawn", "direction": s.direction} for s in todo]
                + [{"role": s.role, "instance": s.index, "action": "retry", "pane": mapping[s.key]} for s in retry])
        report = {"team": team.name, "workspace": ws, "lead": lead_pane, "plan": plan, "spawned": [], "failed": [],
                  "roster": None, "roster_state": None, "dry_run": dry_run}
        if dry_run:
            return report

        data["panes"][lead_pane] = {"role": lead_slot.role, "instance": lead_slot.index, "lead": True,
                                    "team": team.name, "spawned_by": None, "status": "ready", "error": None}
        data["team"] = team.name
        store.save(ws, data)
        lead_label = roles[lead_slot.role].label
        cwd = lead_info.get("cwd") or lead_info.get("foreground_cwd")

        remaining, chain_last = {}, {}
        for s in todo:
            remaining[s.group] = remaining.get(s.group, 0) + 1
        for s in todo:
            anchor_pane = mapping.get(s.anchor, lead_pane)
            target = chain_last.get(s.group)
            if target is None:
                existing = [mapping[x.key] for x in slots if x.group == s.group and x.key in mapping]
                target = existing[-1] if existing else anchor_pane
            # `ratio` = share kept by `target`; equal shares by default (1/(members still to place + target))
            kept = s.ratio if s.ratio else 1.0 / (remaining[s.group] + 1)
            remaining[s.group] -= 1
            try:
                new = h.pane_split(target, s.direction, ratio=kept, cwd=cwd)
            except HerdrError as e:
                report["failed"].append({"role": s.role, "instance": s.index, "error": str(e)[:300]})
                continue
            mapping[s.key] = chain_last[s.group] = new
            data["panes"][new] = {"role": s.role, "instance": s.index, "lead": False, "team": team.name,
                                  "spawned_by": SPAWNER, "status": "starting", "error": None}
            store.save(ws, data)
            _start(h, data, new, roles[s.role], s.index, team, ws, settings, lead_label)
            store.save(ws, data)
            report["spawned"].append({"role": s.role, "instance": s.index, "pane": new,
                                      "status": data["panes"][new]["status"]})

        for s in retry:
            pane = mapping[s.key]
            _start(h, data, pane, roles[s.role], s.index, team, ws, settings, lead_label)
            report["spawned"].append({"role": s.role, "instance": s.index, "pane": pane,
                                      "status": data["panes"][pane]["status"], "retried": True})

        display.apply_all(h, data, roles)
        if todo or retry:    # a no-op re-run must not poke the lead again with the same roster
            report["roster"] = build_roster(team, roles, data, lead_pane)
            report["roster_state"] = _deliver_roster(h, data, lead_info, lead_pane, report["roster"])
        store.save(ws, data)

    bad = [x for x in report["spawned"] if x["status"] != "ready"] + report["failed"]
    if bad:
        h.notify(f"Roles: 팀 '{team.name}' 일부 실패", ", ".join(x["role"] for x in bad))
    return report


def team_down(h, store, ws):
    """Close only the panes this plugin spawned (never the lead, never user-made panes)."""
    if not store.exists(ws):
        return {"closed": [], "kept": []}
    live = {p["pane_id"] for p in h.pane_list(ws)}
    closed, kept = [], []
    with store.lock():
        data = store.load(ws)
        for pid, p in list(data["panes"].items()):
            if p.get("spawned_by") == SPAWNER:
                if pid in live:
                    try:
                        h.pane_close(pid)
                    except HerdrError:
                        pass
                closed.append(pid)
                del data["panes"][pid]
            else:
                p["team"] = None
                p["lead"] = False
                kept.append(pid)
        data.update(team=None, run=None, pending_roster=None)
        store.save(ws, data)
    return {"closed": closed, "kept": kept}


def team_status(h, store, roles, ws, team=None):
    live = {p["pane_id"]: p for p in h.pane_list(ws)}
    data = store.load(ws)
    rows, mapping = [], _mapping(data, data["team"]) if data.get("team") else {}
    if team:
        for s in expand_slots(team):
            pid = mapping.get(s.key)
            rows.append(_row(s.role, s.index, pid, data, live))
    else:
        for pid, p in sorted(data["panes"].items()):
            rows.append(_row(p["role"], p.get("instance", 1), pid, data, live))
    return {"workspace": ws, "team": data.get("team"), "rows": rows, "run": data.get("run")}


def _row(role, instance, pid, data, live):
    if not pid:
        return {"role": role, "instance": instance, "pane": None, "state": "missing", "agent_status": None}
    info = data["panes"].get(pid, {})
    lp = live.get(pid)
    state = info.get("status", "ready") if lp else "gone"
    return {"role": role, "instance": instance, "pane": pid, "state": state, "lead": info.get("lead", False),
            "agent_status": (lp or {}).get("agent_status"), "error": info.get("error")}


def assign(h, store, roles, ws, pane_id, role_name):
    if role_name not in roles:
        raise TeamError(f"unknown role '{role_name}' (known: {', '.join(sorted(roles))})")
    with store.lock():
        data = store.load(ws)
        inst = 1 + sum(1 for p in data["panes"].values() if p.get("role") == role_name and not p.get("team"))
        data["panes"][pane_id] = {"role": role_name, "instance": inst, "lead": False, "team": None,
                                  "spawned_by": None, "status": "ready", "error": None}
        display.apply(h, data, pane_id, roles)
        store.save(ws, data)


def unassign(h, store, ws, pane_id):
    with store.lock():
        data = store.load(ws)
        removed = data["panes"].pop(pane_id, None)
        store.save(ws, data)
    if removed:
        try:
            h.report_metadata(pane_id, display.SOURCE, title="", tokens={"role": "-"})
        except HerdrError:
            pass
    return bool(removed)
