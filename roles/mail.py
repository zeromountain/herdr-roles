"""Messages between team members: who asked whom, and the report that goes back when the work is done.

`roles send` from a team pane records an ask on the target pane. When that pane's agent finishes a turn
(working -> idle/done) the dispatch hook sends its last output -- the last <<<HANDOFF ... >>> block if it printed one --
back to every pane that asked. A request between two members is copied to the lead as one line. A message for an
agent that is busy waits in that pane's outbox and goes out on its next idle, so nobody is interrupted mid-task.

An orchestrator's request to a fleet team's lead (`origin`, see fleet.py) comes from another workspace and is
*sticky*: a lead ends many turns while its members work, so the ask is answered by a new HANDOFF block -- one that
was not already on the lead's screen when the request went out -- or, when the lead forgets the block, by its last
output once the whole team is quiet (nothing asked of a member is still open, nothing waits in an outbox, no member is
working). The report goes to the orchestrator's workspace.
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
# How an agent hands in its result. Shown in every request and briefing; the screen-based HANDOFF block stays as a
# fallback for agents that forget (a long block scrolls off the screen herdr can read, a file never does).
REPORT_HOWTO = f"{CLI} report <<'EOF'\n<한 일, 바뀐 파일, 남은 문제>\nEOF"


def clip(text, is_block):
    """Fit a report into REPORT_CHARS. A HANDOFF block is a summary written top-down, so its beginning is kept;
    raw terminal output ends with the agent's conclusion, so its end is kept."""
    if len(text) <= REPORT_CHARS:
        return text
    if is_block:
        return text[:REPORT_CHARS].rstrip() + "\n…(이하 생략)"
    return "…" + text[-REPORT_CHARS:]


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


def first_line(text, limit=80):
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


ORCHESTRATOR = "오케스트레이터"


def orchestrator_request(text):
    return (f"{TAG} {ORCHESTRATOR}의 요청입니다. 팀원에게 나눠 맡기고, 팀의 일이 모두 끝나면 셸에서 결과를 보고하세요. "
            f"{ORCHESTRATOR}에게 자동으로 보고됩니다 (팀원에게 일을 넘기고 기다리는 중에는 보고하지 마세요):\n"
            f"{REPORT_HOWTO}\n\n{text}")


def track_up(data, target, origin, text, queued, baseline):
    """Track an orchestrator request on `target` (sticky: see the module docstring)."""
    ask = data["asks"].setdefault(target, {"from": [], "sent_at": now_ms(), "seen_working": False})
    if not queued:
        ask.update(sent_at=now_ms(), seen_working=False)
    # a held request must not replace the one the lead is still working on: that one's answer is still due
    ask["from"] = ([a for a in ask["from"] if a["pane"] != origin["pane"] or (queued and not a.get("queued"))]
                   + [{"pane": origin["pane"], "workspace": origin["workspace"], "summary": first_line(text),
                       "queued": queued, "sticky": True, "baseline": baseline}])


def send(h, data, roles, sender, target, text, live, origin=None, lines=200):
    """Prompt the agent in `target`. A request from a team pane is labelled with its sender and tracked, so the
    target's answer is reported back when it finishes; one between two members is copied to the lead.

    `origin` ({pane, workspace}) is an orchestrator in another workspace asking this team's lead.
    """
    if origin:
        prompt = orchestrator_request(text)
        queued = (live.get(target) or {}).get("agent_status") == "working"
        if queued:
            data["outbox"].setdefault(target, []).append(prompt)
            baseline = None             # taken when the held request is finally delivered
        else:
            baseline = handoff.last_block(h, target, lines)
            h.agent_prompt(target, prompt)
        track_up(data, target, origin, text, queued, baseline)
        return "queued" if queued else "sent"
    asker = data["panes"].get(sender) or {}
    run = data.get("run") or {}
    in_workflow = run.get("state") == "running" and (run.get("current") or {}).get("pane") == target
    if not asker.get("team") or sender == target or in_workflow:     # the user's own shell, or a workflow step
        h.agent_prompt(target, text)
        return False
    me = who(data, roles, sender)
    prompt = (f"{TAG} {me}의 요청입니다. 끝나면 셸에서 결과를 보고하세요. {me}에게 자동으로 보고됩니다:\n"
              f"{REPORT_HOWTO}\n\n{text}")
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
                   + [{"pane": sender, "summary": first_line(text), "queued": queued}])
    lead = lead_of(data)
    if lead and lead not in (sender, target):
        post(h, data, lead, f"{TAG} 참조: {me} → {who(data, roles, target)}: {first_line(text)}",
             busy=(live.get(lead) or {}).get("agent_status") == "working")
    return "queued" if queued else "sent"


def _report(h, data, roles, pane, askers, settings, out=None, path=None):
    """`out`/`path`: an explicit `roles report` (its text, and the file holding all of it); else read the screen."""
    p = data["panes"].get(pane) or {}
    out = clip(*handoff.report_text(h, pane, settings["handoff_lines"])) if out is None else clip(out, True)
    me = who(data, roles, pane)
    more = f"(전체 보고: {path})" if path else f"(전체 출력: {CLI} read --role {p.get('role', '')} --lines 200)"
    askers = [a for a in askers if a["pane"] in data["panes"]]
    for a in askers:
        post(h, data, a["pane"], f"{TAG} {me} 작업 완료 보고 (요청: {a['summary']})\n\n{out or '(출력 없음)'}\n\n{more}")
    lead = lead_of(data)
    if lead and lead != pane and lead not in {a["pane"] for a in askers}:
        names = ", ".join(who(data, roles, a["pane"]) for a in askers)
        post(h, data, lead, f"{TAG} 참조: {me} 가 {names} 의 요청을 마쳤습니다: {first_line(out)}")
    return len(askers)


def _team_quiet(h, data, lead):
    """Nothing the lead handed out is still in flight: no open ask or queued message for another pane, nobody working."""
    if any(p != lead and ask.get("from") for p, ask in data["asks"].items()):
        return False
    if any(p != lead and msgs for p, msgs in data["outbox"].items()):
        return False
    try:
        live = h.pane_list(data["workspace"])
    except HerdrError:
        return False
    return not any(p["pane_id"] != lead and p["pane_id"] in data["panes"] and p.get("agent_status") == "working"
                   for p in live)


def _report_up(h, store, data, roles, pane, askers, block, is_block=True, path=None):
    """Deliver a fleet lead's new HANDOFF block to the orchestrator pane(s), queued in *their* workspace when busy.

    The caller already holds the session-wide state lock, which covers every workspace file.
    """
    name = (data.get("parent") or {}).get("name") or data.get("team") or ""
    block = clip(block, is_block)
    me = who(data, roles, pane)
    for a in askers:
        ows = a["workspace"]
        odata = data if ows == data["workspace"] else store.load(ows)
        more = f"(전체 보고: {path})" if path else f"(리드 화면 전체: {CLI} read --fleet {name} --lines 200)"
        post(h, odata, a["pane"], f"{TAG} 팀 '{name}' {me} 작업 완료 보고 (요청: {a['summary']})\n\n{block}\n\n{more}")
        entry = odata["fleet"].get(name)
        if entry:
            entry.update(reports=entry.get("reports", 0) + 1, last_report=first_line(block), reported_at=now_ms())
        if odata is not data:
            store.save(ows, odata)
    return len(askers)


def _save_report(store, ws, pane, text):
    d = os.path.join(store.dir, "reports")
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, f"{ws}-{pane.replace(':', '_')}-{now_ms()}.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(text.rstrip() + "\n")
    return path


def report(h, store, roles, ws, pane, text, settings):
    """`roles report`: the agent in `pane` hands in its result explicitly. The whole text goes to a file; whoever is
    waiting on this pane gets its beginning and the file's path. With nobody waiting, a member reports to its lead and
    a fleet lead to its orchestrator. Returns (who got it, path)."""
    if not text.strip():
        raise ValueError("보고할 내용이 비어 있습니다.")
    with store.lock():
        data = store.load(ws)
        path = _save_report(store, ws, pane, text)
        ask = data["asks"].get(pane) or {"from": []}
        delivered = [a for a in ask["from"] if not a.get("queued")]
        local = [a for a in delivered if not a.get("sticky")]
        up = [a for a in delivered if a.get("sticky")]
        to = [who(data, roles, a["pane"]) for a in local] + [ORCHESTRATOR for _ in up[:1]]
        if local:
            _report(h, data, roles, pane, local, settings, out=text, path=path)
        if up:
            _report_up(h, store, data, roles, pane, up, text, path=path)
        if not delivered:             # nobody asked: hand it to whoever is above this pane
            lead, parent = lead_of(data), data.get("parent")
            unasked = {"summary": "요청 없이 보고", "queued": False}
            if lead == pane and parent:
                _report_up(h, store, data, roles, pane, [{**unasked, "pane": parent["pane"],
                                                          "workspace": parent["workspace"]}], text, path=path)
                to = [ORCHESTRATOR]
            elif lead and lead != pane:
                _report(h, data, roles, pane, [{**unasked, "pane": lead}], settings, out=text, path=path)
                to = [who(data, roles, lead)]
        waiting = [a for a in ask["from"] if a.get("queued")]
        if waiting:
            ask["from"] = waiting
        else:
            data["asks"].pop(pane, None)
        store.save(ws, data)
    return to, path


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
        lines = settings["handoff_lines"]
        local = [a for a in delivered if not a.get("sticky")]
        up = [a for a in delivered if a.get("sticky")]
        # an `idle` seen before any `working` is usually the baseline right after the prompt, not an answer
        ready = bool(delivered) and (ask["seen_working"]
                                     or now_ms() - ask["sent_at"] >= settings["idle_grace_s"] * 1000)
        if local and ready:
            res.append(f"reported:{_report(h, data, roles, pane, local, settings)}")
            local = []
        if up and ready:              # sticky: only a HANDOFF block the request did not find on screen answers it
            block = handoff.last_block(h, pane, lines)
            fresh = [a for a in up if block and block != a.get("baseline")]
            if not fresh and _team_quiet(h, data, pane):     # the lead forgot the block, but the team is done
                # not report_text: a block still on screen is the *previous* answer; the screen's end is this one's
                fresh, block, is_block = up, h.pane_read(pane, lines=lines).strip(), False
            else:
                is_block = True
            if fresh:
                res.append(f"reported-up:{_report_up(h, store, data, roles, pane, fresh, block, is_block)}")
                up = [a for a in up if a not in fresh]
        delivered = local + up
        # held orchestrator requests: note what is on screen before they go out, so an old block can't answer them
        baseline = (handoff.last_block(h, pane, lines)
                    if data["outbox"].get(pane) and any(a.get("sticky") for a in waiting) else None)
        if _flush(h, data, pane):     # after the report: it reads this pane's output, which a new prompt would move
            res.append("flushed")
            if waiting:               # the held requests are delivered now: track them from here
                delivered += [{**a, "queued": False, **({"baseline": baseline} if a.get("sticky") else {})}
                              for a in waiting]
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
