"""Opt-in (`trust_folders = true`): mark a team's folder as trusted for Claude Code and Codex before their agents start.

Both ask "do you trust this folder?" the first time they open one, and an agent waiting on that question is
`blocked`, so `agent start` fails. Answering it records the same thing this module writes:
- Claude Code: `projects.<path>.hasTrustDialogAccepted = true` in ~/.claude.json
- Codex: `[projects."<path>"] trust_level = "trusted"` in ~/.codex/config.toml
A folder the user already decided about in Codex (any trust_level) is left alone.
"""
import json
import os

from .install import _home


def _claude_file():
    cdir = os.environ.get("CLAUDE_CONFIG_DIR")
    return os.path.join(cdir, ".claude.json") if cdir else os.path.join(_home(), ".claude.json")


def _codex_file():
    return os.path.join(os.environ.get("CODEX_HOME") or os.path.join(_home(), ".codex"), "config.toml")


def _replace(path, text):
    tmp = f"{path}.roles-tmp{os.getpid()}"
    with open(tmp, "w") as f:
        f.write(text)
    os.replace(tmp, path)


def trust_claude(folder):
    path = _claude_file()
    try:
        with open(path) as f:
            data = json.load(f)
    except FileNotFoundError:
        data = {}
    except (OSError, json.JSONDecodeError):
        return False                    # unreadable: never overwrite the user's Claude settings
    proj = data.setdefault("projects", {}).setdefault(folder, {})
    if proj.get("hasTrustDialogAccepted"):
        return False
    proj["hasTrustDialogAccepted"] = True
    os.makedirs(os.path.dirname(path), exist_ok=True)
    # Running Claude sessions rewrite this file too; replace it in one step so neither side sees half a file.
    _replace(path, json.dumps(data, ensure_ascii=False, indent=2))
    return True


def trust_codex(folder):
    path = _codex_file()
    try:
        with open(path) as f:
            text = f.read()
    except FileNotFoundError:
        text = ""
    except OSError:
        return False
    header = f'[projects."{folder}"]'
    if header in text:
        return False
    os.makedirs(os.path.dirname(path), exist_ok=True)
    _replace(path, text + ("" if not text or text.endswith("\n") else "\n") + f'\n{header}\ntrust_level = "trusted"\n')
    return True


TRUSTERS = {"claude": trust_claude, "codex": trust_codex}


def ensure(folder, kinds):
    """Trust `folder` for each agent kind in `kinds` that we know how to; returns the kinds newly trusted."""
    if not folder:
        return []
    folder = os.path.realpath(folder)
    return [k for k in sorted(set(kinds)) if k in TRUSTERS and TRUSTERS[k](folder)]
