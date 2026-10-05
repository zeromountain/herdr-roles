"""Workflow engine: hand work from one role to the next when an agent finishes.

Driven entirely by herdr event hooks (no daemon): each `pane.agent_status_changed` event for the pane that
currently holds the work is evaluated against the persisted run state, then the process exits.

Guards against runaway loops: `max_hops` per run, `max_iterations` per step, and a run only ever has a single
active (pane, step), so a duplicate event can never trigger a second handoff.
"""
from . import handoff
from .config import load_workflow
from .herdr import HerdrError
from .state import now_ms


class WorkflowError(RuntimeError):
    pass


def _live(h, ws):
    return {p["pane_id"]: p for p in h.pane_list(ws)}


def choose_pane(data, role, live):
    """A ready pane with this role, preferring one that is not busy."""
    cands = [pid for pid, p in data["panes"].items()
             if p.get("role") == role and pid in live and p.get("status", "ready") == "ready"]
    if not cands:
        return None
    free = [pid for pid in cands if live[pid].get("agent_status") != "working"]
    return (free or cands)[0]


def deliver(h, pane_id, role, text):
    if role.agent:
        h.agent_prompt(pane_id, text)
    else:
        h.pane_run(pane_id, text)


def _activate(h, data, run, step, pane, roles, text):
    deliver(h, pane, roles[step.role], text)
    run["current"] = {"step": step.id, "pane": pane, "sent_at": now_ms(), "seen_working": False}
    run["pending"] = None


def start(h, store, roles, wf, ws, input_text, force=False):
    live = _live(h, ws)
    with store.lock():
        data = store.load(ws)
        old = data.get("run")
        if old and old.get("state") == "running" and not force:
            raise WorkflowError(f"workflow '{old['workflow']}' is already running (use --force to replace it)")
        entry = wf.entry
        pane = choose_pane(data, entry.role, live)
        if not pane:
            raise WorkflowError(f"역할 '{entry.role}'의 pane이 없습니다. 먼저 team-up 또는 assign 하세요.")
        if live[pane].get("agent_status") == "working" and not force:
            raise WorkflowError(f"'{entry.role}' pane {pane}이(가) 작업 중입니다. 끝난 뒤 다시 실행하거나 --force 를 쓰세요.")
        run = {"workflow": wf.name, "state": "running", "hops": 0, "iterations": {entry.id: 1},
               "current": None, "pending": None, "history": [], "started_at": now_ms(), "reason": None}
        text = handoff.render(entry.template, input_text, step=entry.id, from_role="user")
        _activate(h, data, run, entry, pane, roles, text)
        run["history"].append({"step": entry.id, "pane": pane, "at": now_ms()})
        data["run"] = run
        store.save(ws, data)
    return run


def _halt(h, run, reason):
    run.update(state="halted", reason=reason, current=None, pending=None)
    h.notify("Roles: 워크플로우 중단", reason)
    return "halted"


def on_status(h, store, roles, ws, pane_id, status, settings):
    """Handle one agent status event. Returns what happened (for logs/tests) or None when it is not ours."""
    if not store.exists(ws):
        return None
    with store.lock(timeout=20):
        data = store.load(ws)
        run = data.get("run")
        cur = run.get("current") if run else None
        if not run or run.get("state") != "running" or not cur or cur["pane"] != pane_id:
            return None
        if status == "working":
            cur["seen_working"] = True
            store.save(ws, data)
            return "working"
        if status == "blocked":
            h.notify("Roles: 입력 필요", f"{data['panes'].get(pane_id, {}).get('role', pane_id)} 단계가 승인/입력을 기다립니다.")
            return "blocked"
        if status not in ("idle", "done"):
            return None
        # an `idle` seen before any `working` is usually the baseline, not completion
        if not cur["seen_working"] and now_ms() - cur["sent_at"] < settings["idle_grace_s"] * 1000:
            return None
        wf = load_workflow(run["workflow"], roles)
        result = _complete(h, data, run, wf, roles, ws, pane_id, settings)
        store.save(ws, data)
        return result


def _complete(h, data, run, wf, roles, ws, pane_id, settings):
    step = wf.step(run["current"]["step"])
    live = _live(h, ws)
    out = handoff.last_output(h, pane_id, settings["handoff_lines"])
    run["history"].append({"step": step.id, "pane": pane_id, "done_at": now_ms(), "chars": len(out)})
    run["current"] = None
    run["hops"] += 1
    candidates = [s for s in wf.steps if step.id in s.from_ and handoff.when_matches(s.when, out)]
    if not candidates:
        run.update(state="done", reason=None)
        h.notify("Roles: 워크플로우 완료", f"{wf.name}: 마지막 단계 '{step.id}'")
        return "done"
    if run["hops"] >= wf.max_hops:
        return _halt(h, run, f"max_hops({wf.max_hops}) 도달")
    nxt = candidates[0]  # branches: the first matching step wins
    n = run["iterations"].get(nxt.id, 0) + 1
    if n > nxt.max_iterations:
        return _halt(h, run, f"'{nxt.id}' 단계가 max_iterations({nxt.max_iterations})를 초과")
    target = choose_pane(data, nxt.role, live)
    if not target:
        return _halt(h, run, f"역할 '{nxt.role}'의 사용 가능한 pane이 없습니다")
    payload = handoff.collect(h, nxt.handoff, pane_id, live.get(pane_id, {}).get("cwd"),
                              settings["handoff_lines"], output=out)
    text = handoff.render(nxt.template, payload, step=nxt.id, from_role=data["panes"].get(pane_id, {}).get("role", ""))
    run["iterations"][nxt.id] = n
    if nxt.auto:
        _activate(h, data, run, nxt, target, roles, text)
        return f"handoff:{step.id}->{nxt.id}"
    run["pending"] = {"step": nxt.id, "pane": target, "text": text}
    h.notify("Roles: 인계 대기", f"{step.id} → {nxt.id}. `roles advance`로 전달하세요.")
    return f"pending:{step.id}->{nxt.id}"


def advance(h, store, roles, ws):
    with store.lock():
        data = store.load(ws)
        run = data.get("run")
        if not run or run.get("state") != "running" or not run.get("pending"):
            raise WorkflowError("대기 중인 인계가 없습니다.")
        wf = load_workflow(run["workflow"], roles)
        pend = run["pending"]
        step = wf.step(pend["step"])
        if pend["pane"] not in _live(h, ws):
            _halt(h, run, f"인계 대상 pane {pend['pane']}이 사라졌습니다")
            store.save(ws, data)
            raise WorkflowError(run["reason"])
        _activate(h, data, run, step, pend["pane"], roles, pend["text"])
        store.save(ws, data)
    return step.id


def stop(store, ws, reason="stopped by user"):
    with store.lock():
        data = store.load(ws)
        run = data.get("run")
        if run and run.get("state") == "running":
            run.update(state="stopped", reason=reason, current=None, pending=None)
        store.save(ws, data)
    return bool(run)


def on_pane_closed(h, store, ws, pane_id):
    if not store.exists(ws):
        return None
    with store.lock(timeout=20):
        data = store.load(ws)
        info = data["panes"].pop(pane_id, None)
        run = data.get("run")
        if run and run.get("state") == "running":
            cur, pend = run.get("current"), run.get("pending")
            if (cur and cur["pane"] == pane_id) or (pend and pend["pane"] == pane_id):
                _halt(h, run, f"작업 중이던 pane {pane_id}이(가) 닫혔습니다")
        if info or run:
            store.save(ws, data)
        return "removed" if info else None
