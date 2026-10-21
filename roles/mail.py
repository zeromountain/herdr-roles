"""Messages between team members: who asked whom, and the report that goes back when the work is done.

`roles send` from a team pane records an ask on the target pane. When that pane's agent finishes a turn
(working -> idle/done) the dispatch hook sends its last output -- the last <<<HANDOFF ... >>> block if it printed one --
back to every pane that asked. A request between two members is copied to the lead as one line. A message for an
agent that is busy waits in that pane's outbox and goes out on its next idle, so nobody is interrupted mid-task.
"""
import os

from . import handoff
from .herdr import HerdrError
from .state import now_ms

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
# agents run in non-login shells where `roles` may not be on PATH: always hand them the full path
CLI = f"python3 {os.path.join(ROOT, 'bin', 'roles')}"
TAG = "[herdr-roles]"
REPORT_CHARS = 3000


def who(data, roles, pane):
    """'Reviewer(reviewer)' / 'Planner(planner, 리드)' for a team pane, else the pane id."""
    p = data["panes"].get(pane) or {}
    role = roles.get(p.get("role"))
    if not role:
        return pane
    name = p["role"] + ("" if p.get("instance", 1) == 1 else f"-{p['instance']}")
    return f"{role.label}({name}{', 리드' if p.get('lead') else ''})"


def lead_of(data):
    return next((pid for pid, p in data["panes"].items() if p.get("lead") and p.get("team")), None)


def _first_line(text, limit=80):
    line = next((ln.strip() for ln in (text or "").splitlines() if ln.strip()), "")
    return line if len(line) <= limit else line[:limit - 1] + "…"


def _busy(h, pane):
    try:
        return (h.pane_get(pane) or {}).get("agent_status") == "working"
    except HerdrError:
        return False


def post(h, data, pane, text, busy=None):
    """Prompt the agent in `pane` now, or queue the text for its next idle when it is working (or the prompt fails)."""
    if not (_busy(h, pane) if busy is None else busy):
        try:
            h.agent_prompt(pane, text)
            return "sent"
        except HerdrError:
            pass
    data["outbox"].setdefault(pane, []).append(text)
    return "queued"


def send(h, data, roles, sender, target, text, live):
    """Prompt the agent in `target`. A request from a team pane is labelled with its sender and tracked, so the
    target's answer is reported back when it finishes; one between two members is copied to the lead."""
    asker = data["panes"].get(sender) or {}
    run = data.get("run") or {}
    in_workflow = run.get("state") == "running" and (run.get("current") or {}).get("pane") == target
    if not asker.get("team") or sender == target or in_workflow:     # the user's own shell, or a workflow step
        h.agent_prompt(target, text)
        return False
    me = who(data, roles, sender)
    prompt = (f"{TAG} {me}의 요청입니다. 끝나면 마지막에 결과 요약을 <<<HANDOFF ... >>> 블록으로 "
              f"남기세요. {me}에게 자동으로 보고됩니다.\n\n{text}")
    ask = data["asks"].setdefault(target, {"from": [], "sent_at": now_ms(), "seen_working": False})
    # A busy target's next idle ends the task it is on, not this request: hold the request until that idle and
    # start tracking from there, or the old task's output would be reported as this answer.
    queued = (live.get(target) or {}).get("agent_status") == "working"
    if queued:
        data["outbox"].setdefault(target, []).append(prompt)
    else:
        h.agent_prompt(target, prompt)
        ask.update(sent_at=now_ms(), seen_working=False)
    ask["from"] = ([a for a in ask["from"] if a["pane"] != sender]
                   + [{"pane": sender, "summary": _first_line(text), "queued": queued}])
    lead = lead_of(data)
    if lead and lead not in (sender, target):
        post(h, data, lead, f"{TAG} 참조: {me} → {who(data, roles, target)}: {_first_line(text)}",
             busy=(live.get(lead) or {}).get("agent_status") == "working")
    return "queued" if queued else "sent"


def _report(h, data, roles, pane, askers, settings):
    p = data["panes"].get(pane) or {}
    out = handoff.last_output(h, pane, settings["handoff_lines"])
    if len(out) > REPORT_CHARS:
        out = "…" + out[-REPORT_CHARS:]
    me = who(data, roles, pane)
    more = f"(전체 출력: {CLI} read --role {p.get('role', '')} --lines 200)"
    askers = [a for a in askers if a["pane"] in data["panes"]]
    for a in askers:
        post(h, data, a["pane"], f"{TAG} {me} 작업 완료 보고 (요청: {a['summary']})\n\n{out or '(출력 없음)'}\n\n{more}")
    lead = lead_of(data)
    if lead and lead != pane and lead not in {a["pane"] for a in askers}:
        names = ", ".join(who(data, roles, a["pane"]) for a in askers)
        post(h, data, lead, f"{TAG} 참조: {me} 가 {names} 의 요청을 마쳤습니다: {_first_line(out)}")
    return len(askers)


def _flush(h, data, pane):
    queued = data["outbox"].pop(pane, [])
    if not queued:
        return False
    try:
        h.agent_prompt(pane, "\n\n---\n\n".join(queued))    # one prompt: a second one would land mid-turn
        return True
    except HerdrError:
        data["outbox"][pane] = queued
        return False


def on_status(h, store, roles, ws, pane, status, settings):
    """Handle one agent status event: flush what waited for this pane, and report a finished ask to its askers."""
    if status not in ("working", "idle", "done") or not store.exists(ws):
        return None
    with store.lock(timeout=20):
        data = store.load(ws)
        ask = data["asks"].get(pane) or {"from": []}
        delivered = [a for a in ask["from"] if not a.get("queued")]
        waiting = [a for a in ask["from"] if a.get("queued")]
        if status == "working":
            if not delivered or ask["seen_working"]:     # working on something other than a delivered request
                return None
            ask["seen_working"] = True
            store.save(ws, data)
            return "ask:working"
        res = []
        # an `idle` seen before any `working` is usually the baseline right after the prompt, not an answer
        if delivered and (ask["seen_working"] or now_ms() - ask["sent_at"] >= settings["idle_grace_s"] * 1000):
            res.append(f"reported:{_report(h, data, roles, pane, delivered, settings)}")
            delivered = []
        if _flush(h, data, pane):     # after the report: it reads this pane's output, which a new prompt would move
            res.append("flushed")
            if waiting:               # the held requests are delivered now: track them from here
                delivered += [{**a, "queued": False} for a in waiting]
                waiting = []
                ask.update(sent_at=now_ms(), seen_working=False)
        if not res:
            return None
        if delivered or waiting:
            ask["from"] = delivered + waiting
            data["asks"][pane] = ask
        else:
            data["asks"].pop(pane, None)
        store.save(ws, data)
        return ",".join(res)


def on_pane_closed(store, ws, pane):
    if not store.exists(ws):
        return None
    with store.lock(timeout=20):
        data = store.load(ws)
        changed = data["asks"].pop(pane, None) is not None
        changed |= data["outbox"].pop(pane, None) is not None
        for ask in data["asks"].values():
            kept = [a for a in ask["from"] if a["pane"] != pane]
            changed |= len(kept) != len(ask["from"])
            ask["from"] = kept
        if changed:
            store.save(ws, data)
        return "mail:dropped" if changed else None
