"""Collect the payload that one role hands to the next, and render the receiving step's template."""
import os
import re
import subprocess

from .config import parse_when

_MARKER = re.compile(r"<<<HANDOFF\s*(.*?)\s*>>>", re.S)
# Our own instructions ("... 을 <<<HANDOFF ... >>> 블록으로 남기세요") are echoed on the agent's screen; their
# placeholder must never count as the agent's answer.
_PLACEHOLDERS = {"", "...", "…"}


def _blocks(text):
    return [b for b in _MARKER.findall(text) if b.strip() not in _PLACEHOLDERS]


def defuse(text):
    """Text we deliver into another agent's pane, with its HANDOFF opener broken. Echoed on the receiver's screen
    (a lead's request, a member's report), an intact one would open a block there that is not the receiver's
    answer -- and could pair with an unrelated `>>>` further down."""
    return (text or "").replace("<<<HANDOFF", "<<HANDOFF")

_MAX_FILE = 200_000


def last_output(h, pane_id, lines):
    """Terminal text of the pane; if the agent printed a <<<HANDOFF ... >>> block, only its last block."""
    return report_text(h, pane_id, lines)[0]


def report_text(h, pane_id, lines):
    """(text, is_block): the pane's last HANDOFF block, or its terminal text when it printed none."""
    text = h.pane_read(pane_id, lines=lines)
    blocks = _blocks(text)
    return (blocks[-1] if blocks else text).strip(), bool(blocks)


def last_block(h, pane_id, lines):
    """The last <<<HANDOFF ... >>> block on the pane's screen, or None when it printed none."""
    blocks = _blocks(h.pane_read(pane_id, lines=lines))
    return blocks[-1].strip() if blocks else None


def git_diff(cwd):
    if not cwd:
        return ""
    for args in (["diff", "HEAD"], ["diff"]):
        try:
            p = subprocess.run(["git", "-C", cwd, *args], capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired):
            return ""
        if p.returncode == 0 and p.stdout.strip():
            return p.stdout
    return ""


def read_file(path, cwd):
    full = path if os.path.isabs(path) else os.path.join(cwd or ".", path)
    try:
        with open(full, errors="replace") as f:
            return f.read(_MAX_FILE)
    except OSError as e:
        return f"(cannot read {path}: {e})"


def collect(h, kind, pane_id, cwd, lines, output=None):
    """`kind`: last_output | git_diff | none | file:<path>. `output` is the already-read pane text, if any."""
    if kind == "none":
        return ""
    if kind == "git_diff":
        return git_diff(cwd) or "(변경 사항 없음: git diff 결과가 비어 있습니다)"
    if kind.startswith("file:"):
        return read_file(kind[5:], cwd)
    return output if output is not None else last_output(h, pane_id, lines)


def render(template, input_text, **ctx):
    out = template.replace("{{input}}", input_text)
    for k, v in ctx.items():
        out = out.replace("{{" + k + "}}", str(v))
    return out


def when_matches(expr, output):
    if not expr:
        return True
    negate, rx = parse_when(expr)
    hit = bool(rx.search(output or ""))
    return not hit if negate else hit
