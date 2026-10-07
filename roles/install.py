"""Make `roles` usable from agents: link the CLI onto PATH and the agent skill into Claude Code / Codex.

Everything is a symlink into the plugin folder, so reinstalling the plugin (same folder name) updates them in place.
Existing files are never overwritten; a link that points somewhere else (e.g. a dev clone) is kept unless forced.
"""
import os
import subprocess

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
CLI = os.path.join(ROOT, "bin", "roles")
SKILL = os.path.join(ROOT, "skills", "herdr-roles")


def _home():
    return os.environ.get("ROLES_HOME") or os.path.expanduser("~")


def bin_dir():
    return os.environ.get("ROLES_BIN_DIR") or os.path.join(_home(), ".local", "bin")


def skill_targets():
    """(agent label, agent home) for every agent we can install the skill into. Only existing homes are used."""
    return [("Claude Code", os.path.join(_home(), ".claude")),
            ("Codex", os.environ.get("CODEX_HOME") or os.path.join(_home(), ".codex"))]


def _link(src, dst, force=False):
    """Point `dst` at `src`. Returns (status, detail); status is one of
    created / ok (already ours) / replaced (was dangling or forced) / kept (points elsewhere) / blocked (a real file)."""
    if os.path.islink(dst):
        current = os.path.realpath(dst)
        if current == os.path.realpath(src):
            return "ok", ""
        if os.path.exists(current) and not force:
            return "kept", current
        os.remove(dst)
        os.symlink(src, dst)
        return "replaced", current
    if os.path.exists(dst):
        return "blocked", ""
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    os.symlink(src, dst)
    return "created", ""


def _on_login_path(directory):
    """Is `directory` on PATH for this process or for the user's login shell (what agents and hooks use)?"""
    want = os.path.realpath(directory)
    paths = [os.environ.get("PATH", "")]
    shell = os.environ.get("SHELL")
    if shell:
        try:
            paths.append(subprocess.run([shell, "-lc", 'printf %s "$PATH"'], capture_output=True, text=True,
                                        timeout=5).stdout)
        except (OSError, subprocess.SubprocessError):
            pass
    return any(os.path.realpath(p) == want for joined in paths for p in joined.split(os.pathsep) if p)


def _describe(what, dst, status, detail):
    if status in ("created", "replaced"):
        return f"  ✓ {what}: {dst} 연결했어요" + (f" (이전 링크: {detail})" if detail else "")
    if status == "ok":
        return f"  ✓ {what}: 이미 연결돼 있어요 ({dst})"
    if status == "kept":
        return f"  - {what}: {dst} 가 다른 곳({detail})을 가리켜 그대로 뒀어요. 바꾸려면 `roles install --force`"
    return f"  ! {what}: {dst} 에 링크가 아닌 파일이 있어 건드리지 않았어요"


def install(force=False):
    """Link the CLI and the skill. Returns the lines to show; a failed link is reported, never raised."""
    lines = []
    dst = os.path.join(bin_dir(), "roles")
    try:
        status, detail = _link(CLI, dst, force)
        lines.append(_describe("roles 명령", dst, status, detail))
        if status != "blocked" and not _on_login_path(bin_dir()):
            lines.append(f"  ! {bin_dir()} 가 PATH 에 없어 `roles` 를 찾지 못합니다. 한 번만 추가하세요:\n"
                         f"      echo 'export PATH=\"{bin_dir()}:$PATH\"' >> ~/.zprofile")
    except OSError as e:
        lines.append(f"  ! roles 명령: {dst} 연결 실패 ({e})")
    for label, home in skill_targets():
        if not os.path.isdir(home):
            continue
        dst = os.path.join(home, "skills", "herdr-roles")
        try:
            status, detail = _link(SKILL, dst, force)
            lines.append(_describe(f"{label} 스킬", dst, status, detail))
        except OSError as e:
            lines.append(f"  ! {label} 스킬: {dst} 연결 실패 ({e})")
    return lines
