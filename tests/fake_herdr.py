#!/usr/bin/env python3
"""A tiny stand-in for the herdr CLI, backed by a JSON file ($FAKE_HERDR_STATE).

It speaks the same JSON envelope as the real binary for the commands the plugin uses and records every
invocation, so tests can assert on splits, ratios, prompts and notifications.
"""
import json
import os
import sys

PATH = os.environ["FAKE_HERDR_STATE"]
args = sys.argv[1:]


def load():
    with open(PATH) as f:
        return json.load(f)


def save(s):
    with open(PATH, "w") as f:
        json.dump(s, f)


def ok(result):
    print(json.dumps({"id": "fake", "result": result}))
    sys.exit(0)


def fail(msg):
    print(json.dumps({"id": "fake", "error": {"code": "fake", "message": msg}}))
    sys.exit(1)


def opt(name, default=None, many=False):
    vals = [args[i + 1] for i, a in enumerate(args[:-1]) if a == name]
    if many:
        return vals
    return vals[-1] if vals else default


def public(p):
    return {k: v for k, v in p.items() if k not in ("output", "ran", "prompts", "meta", "agent_name")}


s = load()
s["calls"].append(args)
cmd = " ".join(args[:2])

if cmd == "pane list":
    ws = opt("--workspace")
    ok({"panes": [public(p) for p in s["panes"].values() if not ws or p["workspace_id"] == ws]})
elif cmd == "pane get":
    p = s["panes"].get(args[2]) or fail(f"pane not found: {args[2]}")
    ok({"pane": public(p)})
elif cmd == "pane split":
    target = s["panes"].get(opt("--pane")) or fail("pane not found")
    if s.get("fail_split_after") is not None and len(s["splits"]) >= s["fail_split_after"]:
        fail("split failed (fake)")
    ws = target["workspace_id"]
    s["next"][ws] = s["next"].get(ws, 1) + 1
    pid = f"{ws}:p{s['next'][ws]}"
    s["panes"][pid] = {"pane_id": pid, "workspace_id": ws, "cwd": opt("--cwd", target["cwd"]), "agent": None,
                       "agent_status": "unknown", "output": "", "ran": [], "prompts": [], "meta": {}}
    s["splits"].append({"target": target["pane_id"], "direction": opt("--direction"),
                        "ratio": float(opt("--ratio")) if opt("--ratio") else None, "new": pid})
    save(s)
    ok({"pane": public(s["panes"][pid])})
elif cmd == "pane close":
    s["panes"].pop(args[2], None)
    save(s)
    ok({"type": "ok"})
elif cmd == "pane run":
    (s["panes"].get(args[2]) or fail("pane not found"))["ran"].append(args[3])
    save(s)
    ok({"type": "ok"})
elif cmd == "pane send-text":
    p = s["panes"].get(args[2]) or fail("pane not found")
    p.setdefault("typed", []).append(args[3])
    save(s)
    ok({"type": "ok"})
elif cmd == "pane send-keys":
    p = s["panes"].get(args[2]) or fail("pane not found")
    p.setdefault("keys", []).append(args[3:])
    save(s)
    ok({"type": "ok"})
elif cmd == "pane rename":
    ok({"type": "ok"})
elif cmd == "pane read":
    print((s["panes"].get(args[2]) or fail("pane not found")).get("output", ""), end="")
    sys.exit(0)
elif cmd == "pane report-metadata":
    p = s["panes"].get(args[2]) or fail("pane not found")
    p["meta"] = {"title": opt("--title"), "tokens": opt("--token", many=True),
                 "state_labels": opt("--state-label", many=True), "seq": opt("--seq")}
    save(s)
    ok({"type": "ok"})
elif cmd == "agent start":
    p = s["panes"].get(opt("--pane")) or fail("pane not found")
    if opt("--kind") in s.get("bad_kinds", []):
        fail(f"unsupported agent kind: {opt('--kind')}")
    p.update(agent=opt("--kind"), agent_status="idle", agent_name=args[2])
    save(s)
    ok({"agent": {"name": args[2], "pane_id": p["pane_id"], "interactive_ready": True, "agent_status": "idle"}})
elif cmd == "agent prompt":
    target = args[2]
    p = s["panes"].get(target) or next((x for x in s["panes"].values() if x.get("agent_name") == target), None)
    if not p or not p.get("agent"):
        fail(f"no agent at {target}")
    if p.get("unnamed"):    # agent resumed after a restart: still running, but its name registration is gone
        fail(f"agent {target} is not an active named agent")
    p["prompts"].append(args[3])
    save(s)
    ok({"type": "agent_prompted"})
elif cmd == "notification show":
    s["notifications"].append({"title": args[2], "body": opt("--body")})
    save(s)
    ok({"type": "ok"})
else:
    fail(f"fake herdr: unsupported command {' '.join(args)}")
