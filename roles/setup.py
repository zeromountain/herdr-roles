"""First-run wizard: asks one question at a time and writes roles.toml, teams/<name>.toml and config.toml.

`team-up` runs it when no role is configured yet; `roles setup` runs it on demand.
All I/O goes through `ask`/`say` so the flow is testable without a terminal.
"""
import json
import os
import re

from . import config

NAME_RE = re.compile(r"^[a-z][a-z0-9_-]*$")
AGENTS = ("claude", "codex", "gemini", "opencode")
KIND_AGENT, KIND_COMMAND, KIND_SHELL = "1", "2", "3"


class SetupAborted(RuntimeError):
    pass


def needs_setup(cdir=None):
    """True when there is nothing to build a team from: no roles.toml, or one without any [roles.*] table."""
    try:
        return not config.load_roles(cdir)
    except config.ConfigError as e:
        # a missing/empty file means "not configured"; a malformed one must surface, not be overwritten
        return "config not found" in str(e) or "no [roles.*]" in str(e)


def _q(s):
    """TOML basic string. json.dumps output is valid TOML for everything we write (quotes, backslashes, unicode)."""
    return json.dumps(s, ensure_ascii=False)


def _ask_choice(ask, say, title, options, default=1):
    say(title)
    for i, (label, _) in enumerate(options, 1):
        say(f"  {i}. {label}")
    while True:
        raw = ask(f"번호 [{default}]: ").strip()
        if not raw:
            return options[default - 1][1]
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return options[int(raw) - 1][1]
        say("목록에 있는 번호를 입력하세요.")


def _ask_yes_no(ask, say, text, default=False):
    hint = "Y/n" if default else "y/N"
    while True:
        raw = ask(f"{text} ({hint}): ").strip().lower()
        if not raw:
            return default
        if raw in ("y", "yes", "예", "ㅇ"):
            return True
        if raw in ("n", "no", "아니오", "ㄴ"):
            return False
        say("y 또는 n 으로 답하세요.")


def _ask_name(ask, say, prompt, default=None, taken=()):
    while True:
        name = ask(prompt).strip().lower() or (default or "")
        if not NAME_RE.match(name):
            say("영문 소문자로 시작하고 소문자·숫자·-·_ 만 쓸 수 있어요.")
        elif name in taken:
            say(f"'{name}' 은(는) 이미 추가했어요.")
        else:
            return name


def _ask_role(ask, say, taken, index):
    say(f"\n── 역할 {index} ──")
    name = _ask_name(ask, say, "역할 이름(영문 소문자·숫자·-·_, 예: planner): ", taken=taken)
    label = ask(f"화면에 보일 이름 [{name.capitalize()}]: ").strip() or name.capitalize()
    kind = _ask_choice(ask, say, "이 역할은 무엇으로 실행할까요?",
                       [("AI 에이전트 (claude, codex …)", KIND_AGENT), ("셸 명령 (개발 서버, watch 등)", KIND_COMMAND),
                        ("그냥 셸", KIND_SHELL)])
    role = {"name": name, "label": label, "agent": None, "command": None, "prompt": ""}
    if kind == KIND_AGENT:
        agent = _ask_choice(ask, say, "어떤 에이전트를 쓸까요?", [(a, a) for a in AGENTS] + [("직접 입력", None)])
        while not agent:
            agent = ask("에이전트 종류(herdr agent start --kind 값): ").strip() or None
        role["agent"] = agent
        role["prompt"] = ask("이 역할의 담당 업무를 한두 문장으로 알려주세요 (건너뛰려면 Enter): ").strip()
    elif kind == KIND_COMMAND:
        while not role["command"]:
            role["command"] = ask("실행할 명령 (예: pnpm dev): ").strip() or None
    return role


def collect(ask, say):
    """Interview the user. Returns {'roles': [...], 'lead': name, 'team': name}."""
    say("설정된 역할이 없어요. 역할을 하나씩 정해볼게요. (Ctrl+C 로 중단)")
    roles = []
    while True:
        roles.append(_ask_role(ask, say, {r["name"] for r in roles}, len(roles) + 1))
        if not _ask_yes_no(ask, say, "역할을 더 추가할까요?", default=len(roles) < 2):
            break
    lead = roles[0]["name"]
    if len(roles) > 1:
        lead = _ask_choice(ask, say, "\n지금 pane(team-up 을 실행한 곳)이 맡을 리드 역할은?",
                           [(f"{r['name']} ({r['label']})", r["name"]) for r in roles])
    team = _ask_name(ask, say, "팀 이름 [custom]: ", default="custom")
    return {"roles": roles, "lead": lead, "team": team}


def render_roles(roles):
    out = ["# `roles setup` 이 만든 파일입니다. 직접 고쳐도 됩니다.", ""]
    for r in roles:
        out.append(f"[roles.{r['name']}]")
        out.append(f"label = {_q(r['label'])}")
        if r["agent"]:
            out.append(f"agent = {_q(r['agent'])}")
        if r["command"]:
            out.append(f"command = {_q(r['command'])}")
        if r["prompt"]:
            out.append(f"prompt = {_q(r['prompt'])}")
        out.append("")
    return "\n".join(out)


def render_team(answers):
    lead = answers["lead"]
    out = ["# `roles setup` 이 만든 팀입니다. split/of 를 생략하면 앞 멤버를 기준으로 자동 배치됩니다.", "",
           "[team]", f"name = {_q(answers['team'])}", "", "[[member]]", f"role = {_q(lead)}", "lead = true", ""]
    for r in answers["roles"]:
        if r["name"] != lead:
            out += ["[[member]]", f"role = {_q(r['name'])}", ""]
    return "\n".join(out)


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def _point_default_team(cdir, team):
    """Make `team-up` without --team find the new team, without disturbing other settings."""
    path = os.path.join(cdir, "config.toml")
    if not os.path.exists(path):
        _write(path, f"[settings]\ndefault_team = {_q(team)}\n")
        return
    current = config.load_settings(cdir)["default_team"]
    if current and os.path.exists(os.path.join(cdir, "teams", f"{current}.toml")):
        return                      # already points at a real team: leave the user's choice alone
    with open(path, encoding="utf-8") as f:
        text = f.read()
    line = f"default_team = {_q(team)}"
    if re.search(r"^default_team\s*=.*$", text, re.M):
        text = re.sub(r"^default_team\s*=.*$", lambda _: line, text, count=1, flags=re.M)
    elif re.search(r"^\[settings\]\s*$", text, re.M):
        text = re.sub(r"^\[settings\]\s*$", lambda m: m.group(0) + "\n" + line, text, count=1, flags=re.M)
    else:
        text = f"[settings]\n{line}\n\n" + text
    _write(path, text)


def run_wizard(ask=None, say=None, cdir=None):
    """Interview, confirm, write. Returns the new team's name; raises SetupAborted if the user declines."""
    ask, say = ask or input, say or print       # looked up at call time, not bound at import
    cdir = cdir or config.config_dir()
    answers = collect(ask, say)
    team_path = os.path.join(cdir, "teams", f"{answers['team']}.toml")
    say("\n── 요약 ──")
    for r in answers["roles"]:
        how = r["agent"] or (f"명령 `{r['command']}`" if r["command"] else "셸")
        say(f"  - {r['name']} ({r['label']}): {how}" + ("  ← 리드" if r["name"] == answers["lead"] else ""))
    if os.path.exists(team_path):
        say(f"  ! 팀 '{answers['team']}' 파일이 이미 있어 덮어씁니다.")
    if os.path.exists(os.path.join(cdir, "roles.toml")):
        say("  ! 역할이 비어 있던 기존 roles.toml 을 덮어씁니다.")
    if not _ask_yes_no(ask, say, "이대로 저장할까요?", default=True):
        raise SetupAborted("저장하지 않고 중단했습니다.")
    _write(os.path.join(cdir, "roles.toml"), render_roles(answers["roles"]))
    _write(team_path, render_team(answers))
    _point_default_team(cdir, answers["team"])
    say(f"저장했어요: {cdir}")
    return answers["team"]
