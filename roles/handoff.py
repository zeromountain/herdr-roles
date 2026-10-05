"""Collect the payload that one role hands to the next, and render the receiving step's template."""
import os
import re
import subprocess

from .config import parse_when

_MARKER = re.compile(r"<<<HANDOFF\s*(.*?)\s*>>>", re.S)
_MAX_FILE = 200_000


def last_output(h, pane_id, lines):
    """Terminal text of the pane; if the agent printed a <<<HANDOFF ... >>> block, only its last block."""
    text = h.pane_read(pane_id, lines=lines)
    blocks = _MARKER.findall(text)
    return (blocks[-1] if blocks else text).strip()


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
