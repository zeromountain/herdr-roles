"""Display projection: role -> herdr pane metadata (title / tokens / state labels).

Metadata is display-only and is NOT restored by herdr after a server restart, so the state file stays the
source of truth and `reapply` (a startup hook) re-projects it.
"""
from .herdr import HerdrError
from .state import next_seq

SOURCE = "plugin:herdr-roles"


def apply(h, data, pane_id, roles):
    info = data["panes"].get(pane_id)
    if not info:
        return
    role = roles.get(info["role"])
    label = role.label if role else info["role"]
    inst = info.get("instance", 1)
    title = f"[{label}{'' if inst == 1 else f' #{inst}'}]"
    tokens = {"role": info["role"], "team": info.get("team") or "-", "lead": "1" if info.get("lead") else "0"}
    if info.get("status") == "failed":
        tokens["spawn"] = "failed"
        title += " ✗"
    state_labels = {
        "idle": f"{label} · 대기",
        "working": f"{label} · 작업 중",
        "blocked": f"{label} · 입력 필요",
        "done": f"{label} · 완료",
    }
    h.report_metadata(pane_id, SOURCE, title=title, tokens=tokens, state_labels=state_labels, seq=next_seq(data))


def apply_all(h, data, roles):
    """Re-project every tracked pane. Returns the pane ids that could not be updated."""
    failed = []
    for pid in list(data["panes"]):
        try:
            apply(h, data, pid, roles)
        except HerdrError:
            failed.append(pid)
    return failed
