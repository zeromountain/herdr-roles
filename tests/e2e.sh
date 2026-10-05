#!/bin/sh
# End-to-end check against a REAL herdr, in an isolated session (`roles-e2e`). Never touches the default session:
# every herdr call below carries an explicit HERDR_SOCKET_PATH (inside a herdr pane that variable would
# otherwise win over HERDR_SESSION and point at your real session).
#
# Spawns two real `claude` agents with one-line prompts, so it costs a few tokens. Needs `claude` on PATH.
set -eu

ROOT=$(cd "$(dirname "$0")/.." && pwd)
SESSION=roles-e2e
SOCK="$HOME/.config/herdr/sessions/$SESSION/herdr.sock"
CFG="$HOME/.config/herdr/plugins/config/herdr-roles"
# claude asks "do you trust this folder?" per exact directory (not inherited by subfolders), and `agent start`
# then reports "blocked". Use a folder you have already trusted: $HOME by default (override: E2E_WORKDIR=/path).
WORK="${E2E_WORKDIR:-$HOME}"
export HERDR_SOCKET_PATH="$SOCK" HERDR_SESSION="$SESSION" ROLES_DEBUG=1
unset HERDR_PANE_ID HERDR_WORKSPACE_ID HERDR_TAB_ID

pass() { printf '  ok   %s\n' "$1"; }
die()  { printf '  FAIL %s\n' "$1" >&2; exit 1; }
py()   { python3 -c "$@"; }
panes() { herdr pane list; }

cleanup() {
  set +e
  herdr plugin unlink herdr-roles >/dev/null 2>&1
  herdr session stop "$SESSION" >/dev/null 2>&1
  herdr session delete "$SESSION" >/dev/null 2>&1
  rm -rf "$CFG"
  [ -d "$CFG.e2e-bak" ] && mv "$CFG.e2e-bak" "$CFG"
  rm -f "$SCRATCH_OUT1" "$SCRATCH_OUT2"
  # state this run created (keyed by session, so the default session's state is never touched)
  rm -rf "$HOME/.local/state/herdr/plugins/herdr-roles/$SESSION"
  rm -f "$HOME/.local/state/herdr/plugins/herdr-roles/dispatch.log"
}
SCRATCH_OUT1=$(mktemp); SCRATCH_OUT2=$(mktemp)
trap cleanup EXIT

echo "# setup"
[ -d "$CFG" ] && mv "$CFG" "$CFG.e2e-bak"
(cd "$ROOT" && nohup herdr --session "$SESSION" server >"$SCRATCH_OUT1" 2>&1 &)
i=0; while [ ! -S "$SOCK" ] && [ $i -lt 40 ]; do sleep 0.25; i=$((i+1)); done
[ -S "$SOCK" ] || die "isolated server did not start"
herdr plugin link "$ROOT" >/dev/null || die "plugin link"
mkdir -p "$CFG/teams" "$CFG/workflows"
cat > "$CFG/roles.toml" <<'E'
[roles.planner]
label = "Planner"
agent = "claude"
[roles.implementer]
label = "Implementer"
agent = "claude"
prompt = "Keep every answer under ten words."
[roles.runner]
label = "Runner"
command = "echo runner-ready"
E
cat > "$CFG/teams/e2e.toml" <<'E'
[[member]]
role = "planner"
lead = true
[[member]]
role = "implementer"
split = "right"
of = "planner"
[[member]]
role = "runner"
split = "down"
of = "implementer"
E
cat > "$CFG/workflows/e2e.toml" <<'E'
[workflow]
name = "e2e"
[[step]]
id = "plan"
role = "planner"
[[step]]
id = "build"
role = "implementer"
from = "plan"
auto = true
template = "Reply with exactly BUILD-DONE. Context follows:\n{{input}}"
E
pass "isolated session + plugin linked"

echo "# lead pane"
WS=$(herdr workspace create --cwd "$WORK" --label e2e | py 'import json,sys;r=json.load(sys.stdin)["result"];print(r["workspace"]["workspace_id"])')
LEAD=$(panes | py 'import json,sys;print(json.load(sys.stdin)["result"]["panes"][0]["pane_id"])')
# Start claude the way a user does (typed into the pane), NOT via `agent start`: the real lead has no name.
herdr pane run "$LEAD" claude >/dev/null || die "start claude in lead pane"
i=0; while [ $i -lt 30 ]; do
  st=$(panes | py 'import json,sys;p=json.load(sys.stdin)["result"]["panes"][0];print(p.get("agent"),p.get("agent_status"))')
  [ "$st" = "claude idle" ] && break; sleep 2; i=$((i+1))
done
[ "$st" = "claude idle" ] || { herdr pane read "$LEAD" --source visible --lines 25 >&2 || true; die "lead claude not ready (got: $st)"; }
pass "lead $LEAD is a manually started claude agent (unnamed) in $WS"

echo "# team-up (1 existing + 2 spawned)"
"$ROOT/bin/roles" team-up --team e2e --pane "$LEAD" || die "team-up"
N=$(panes | py 'import json,sys;print(len(json.load(sys.stdin)["result"]["panes"]))')
[ "$N" = 3 ] && pass "3 panes (lead + 2 spawned)" || die "expected 3 panes, got $N"
panes | py '
import json,sys
ps={p["pane_id"]:p for p in json.load(sys.stdin)["result"]["panes"]}
roles={p.get("tokens",{}).get("role"):p for p in ps.values()}
assert set(roles)=={"planner","implementer","runner"}, roles.keys()
assert roles["implementer"].get("agent")=="claude", "implementer agent"
assert roles["planner"]["title"]=="[Planner]", roles["planner"].get("title")
print("  ok   roles/titles/agents as expected")' || die "metadata check"
"$ROOT/bin/roles" team-up --team e2e --pane "$LEAD" --json | py 'import json,sys;d=json.load(sys.stdin);assert d["plan"]==[],d["plan"];print("  ok   re-run is idempotent")' || die "idempotency"

echo "# workflow driven by real status events"
i=0; while [ $i -lt 60 ]; do   # the lead is still answering the roster; `run` refuses a busy entry pane
  st=$(panes | py 'import json,sys;print([p["agent_status"] for p in json.load(sys.stdin)["result"]["panes"] if p["pane_id"]=="'"$LEAD"'"][0])')
  case "$st" in idle|done) break;; esac
  sleep 2; i=$((i+1))
done
case "$st" in idle|done) ;; *) herdr pane read "$LEAD" --source visible --lines 25 >&2 || true; die "lead never became idle (status=$st)";; esac
"$ROOT/bin/roles" run --workflow e2e --input "Reply with exactly PLAN-DONE." --pane "$LEAD" || die "run"
i=0; state=""
while [ $i -lt 120 ]; do
  state=$("$ROOT/bin/roles" status --json --pane "$LEAD" | py 'import json,sys;r=json.load(sys.stdin)["run"] or {};print(r.get("state"),r.get("current") and r["current"]["step"])')
  case "$state" in "done"*) break;; esac
  sleep 2; i=$((i+1))
done
case "$state" in
  "done"*) pass "workflow reached done (hook-driven plan → build)";;
  *) "$ROOT/bin/roles" status --pane "$LEAD" >&2 || true
     echo "--- dispatch.log" >&2; cat "$HOME/.local/state/herdr/plugins/herdr-roles/dispatch.log" >&2 || true
     die "workflow state=$state";;
esac
IMPL=$(panes | py 'import json,sys;print([p["pane_id"] for p in json.load(sys.stdin)["result"]["panes"] if p.get("tokens",{}).get("role")=="implementer"][0])')
if "$ROOT/bin/roles" read --role implementer --lines 400 --pane "$LEAD" | grep -q "BUILD-DONE"; then
  pass "implementer received the handoff and answered"
else
  echo "--- implementer screen" >&2; herdr pane read "$IMPL" --source visible --lines 60 >&2 || true
  echo "--- dispatch.log" >&2; cat "$HOME/.local/state/herdr/plugins/herdr-roles/dispatch.log" >&2 || true
  die "no BUILD-DONE in implementer output"
fi

echo "# restart: startup hook re-applies metadata; team-up revives the agents that died with the server"
herdr server stop >/dev/null 2>&1 || true; i=0; while herdr status server >/dev/null 2>&1 && [ $i -lt 40 ]; do sleep 0.25; i=$((i+1)); done
(cd "$ROOT" && nohup herdr --session "$SESSION" server >"$SCRATCH_OUT2" 2>&1 &)
i=0; while ! herdr status server >/dev/null 2>&1 && [ $i -lt 40 ]; do sleep 0.25; i=$((i+1)); done
sleep 4
panes | py '
import json,sys
ps=json.load(sys.stdin)["result"]["panes"]
titled=[p for p in ps if p.get("title","").startswith("[")]
assert len(titled)==3, [p.get("title") for p in ps]
print("  ok   titles restored after restart")' || die "reapply after restart"
# herdr tries `claude --resume <id>` in every restored pane. While that runs the pane briefly reports an agent,
# and it often fails ("No conversation found"). Wait until the attempt has settled, then team-up must revive
# exactly the members whose agent is really gone, and never create a pane.
i=0; while [ $i -lt 25 ]; do
  a=$(panes | py 'import json,sys;print([p.get("agent") or "none" for p in json.load(sys.stdin)["result"]["panes"] if p.get("tokens",{}).get("role")=="implementer"][0])')
  [ "$a" = none ] && break; sleep 2; i=$((i+1))
done
sleep 3
"$ROOT/bin/roles" team-up --team e2e --pane "$LEAD" --json | py '
import json,sys
d=json.load(sys.stdin)
acts={(p["role"],p["action"]) for p in d["plan"]}
assert acts <= {("implementer","retry")}, acts
assert d["spawned"]==[] or all(s.get("retried") for s in d["spawned"]), d["spawned"]
print("  ok   team-up after restart creates no new panes (plan: %s)" % (sorted(acts) or "nothing to do"))' || die "post-restart team-up"
N=$(panes | py 'import json,sys;print(len(json.load(sys.stdin)["result"]["panes"]))')
[ "$N" = 3 ] && pass "still 3 panes" || die "expected 3 panes, got $N"
panes | py '
import json,sys
ps={p.get("tokens",{}).get("role"):p for p in json.load(sys.stdin)["result"]["panes"]}
assert ps["implementer"].get("agent")=="claude", ps["implementer"]
print("  ok   implementer agent is running")' || die "implementer agent after restart"
"$ROOT/bin/roles" send --role implementer --text "Reply with exactly SENT-AFTER-RESTART." --pane "$LEAD" >/dev/null || die "send after restart"
i=0; while [ $i -lt 40 ]; do
  "$ROOT/bin/roles" read --role implementer --lines 60 --pane "$LEAD" | grep -q "SENT-AFTER-RESTART" && break
  sleep 2; i=$((i+1))
done
[ $i -lt 40 ] && pass "prompt reaches the implementer after its agent was revived" || die "no SENT-AFTER-RESTART in implementer output"

echo "# manifest actions and plugin panes"
herdr plugin action invoke herdr-roles.status >/dev/null || die "action status"
pass "action herdr-roles.status invoked"
BEFORE=$(panes | py 'import json,sys;print(",".join(sorted(p["pane_id"] for p in json.load(sys.stdin)["result"]["panes"])))')
herdr plugin pane open --plugin herdr-roles --entrypoint board >/dev/null || die "open board pane"
sleep 4
NEWP=$(panes | py '
import json,sys
before=set("'"$BEFORE"'".split(","))
new=[p["pane_id"] for p in json.load(sys.stdin)["result"]["panes"] if p["pane_id"] not in before]
print(new[0] if new else "")')
[ -n "$NEWP" ] || die "board pane did not open"
herdr pane read "$NEWP" --source visible --lines 30 | grep -q "herdr-roles  workspace" && pass "board pane $NEWP renders the team status" || die "board pane shows no status"
herdr pane close "$NEWP" >/dev/null

echo "# team-down"
"$ROOT/bin/roles" team-down --pane "$LEAD" >/dev/null
N=$(panes | py 'import json,sys;print(len(json.load(sys.stdin)["result"]["panes"]))')
[ "$N" = 1 ] && pass "only the lead is left" || die "expected 1 pane after team-down, got $N"
echo "ALL E2E CHECKS PASSED"
