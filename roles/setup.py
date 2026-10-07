"""First-run wizard: asks one question at a time and writes roles.toml, teams/<name>.toml and config.toml.

The first question offers company-style presets (presets.py) or building the roles one by one.

`team-up` runs it when no role is configured yet; `roles setup` runs it on demand.
All I/O goes through `ask`/`say` so the flow is testable without a terminal.
"""
import json
import os
import re
import sys

from . import config
from .presets import AGENTS as PRESET_AGENTS, PRESETS, model_choices, recommended_model

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


def _ask_agent(ask, say, title):
    agent = _ask_choice(ask, say, title, [(a, a) for a in AGENTS] + [("직접 입력", None)])
    while not agent:
        agent = ask("에이전트 종류(herdr agent start --kind 값): ").strip() or None
    return agent


def _ask_role(ask, say, taken, index):
    say(f"\n── 역할 {index} ──")
    name = _ask_name(ask, say, "역할 이름(영문 소문자·숫자·-·_, 예: planner): ", taken=taken)
    label = ask(f"화면에 보일 이름 [{name.capitalize()}]: ").strip() or name.capitalize()
    kind = _ask_choice(ask, say, "이 역할은 무엇으로 실행할까요?",
                       [("AI 에이전트 (claude, codex …)", KIND_AGENT), ("셸 명령 (개발 서버, watch 등)", KIND_COMMAND),
                        ("그냥 셸", KIND_SHELL)])
    role = {"name": name, "label": label, "agent": None, "command": None, "prompt": ""}
    if kind == KIND_AGENT:
        role["agent"] = _ask_agent(ask, say, "어떤 에이전트를 쓸까요?")
        role["prompt"] = ask("이 역할의 담당 업무를 한두 문장으로 알려주세요 (건너뛰려면 Enter): ").strip()
    elif kind == KIND_COMMAND:
        while not role["command"]:
            role["command"] = ask("실행할 명령 (예: pnpm dev): ").strip() or None
    return role


def _ask_lead_and_team(ask, say, roles, lead, team):
    """`lead`/`team` are the defaults offered; Enter keeps them."""
    if len(roles) > 1:
        names = [r["name"] for r in roles]
        lead = _ask_choice(ask, say, "\n지금 pane(team-up 을 실행한 곳)이 맡을 리드 역할은?",
                           [(f"{r['name']} ({r['label']})", r["name"]) for r in roles], default=names.index(lead) + 1)
    team = _ask_name(ask, say, f"팀 이름 [{team}]: ", default=team)
    return lead, team


def _ask_preset_agents(ask, say, preset_roles):
    """{role name: agent} — the recommended mapping, one picked per role, or one agent for all."""
    recommended = {r["name"]: r["agent"] for r in preset_roles}
    how = _ask_choice(ask, say, "\n역할별 에이전트를 어떻게 정할까요?",
                      [("추천 구성 그대로 (기획·리뷰·분석 claude, 구현 codex)", "recommended"),
                       ("역할마다 고르기", "each")] + [(f"모두 {a}", a) for a in PRESET_AGENTS])
    if how == "recommended":
        return recommended
    if how != "each":
        return {name: how for name in recommended}
    picked = {}
    for r in preset_roles:
        picked[r["name"]] = _ask_choice(ask, say, f"{r['name']} ({r['label']}) 의 에이전트는?",
                                        [(a + ("  (추천)" if a == r["agent"] else ""), a) for a in PRESET_AGENTS],
                                        default=PRESET_AGENTS.index(r["agent"]) + 1)
    return picked


MODEL_CUSTOM = "\0custom"


def _ask_model(ask, say, title, agent, recommended=None):
    """A `--model` value, or None to leave the agent CLI on its own default. Enter picks `recommended`."""
    options = ([("기본값 (CLI 설정을 따름)", None)]
               + [(m + ("  (추천)" if m == recommended else ""), m) for m in model_choices(agent)]
               + [("직접 입력", MODEL_CUSTOM)])
    values = [v for _, v in options]
    model = _ask_choice(ask, say, title, options, default=values.index(recommended) + 1 if recommended in values else 1)
    if model == MODEL_CUSTOM:
        model = ask(f"{agent} 모델 이름 (--model 값, 비우면 기본값): ").strip() or None
    return model


def _ask_preset_models(ask, say, roles, preset_roles):
    """{role name: model or None}: the preset's recommendation per role, the CLI defaults, or picked by hand."""
    by_name = {r["name"]: r for r in preset_roles}
    recommended = {r["name"]: recommended_model(by_name[r["name"]], r["agent"]) for r in roles}
    say("\n역할별 추천 모델:")
    for r in roles:
        say(f"  - {r['name']}: {r['agent']} {recommended[r['name']] or '기본값'}")
    if any(r["agent"] == "codex" and not recommended[r["name"]] for r in roles):
        say("  (codex 모델 목록(~/.codex/models_cache.json)에 추천 모델이 없어 해당 역할은 codex 기본 모델을 씁니다)")
    how = _ask_choice(ask, say, "모델은 어떻게 정할까요?",
                      [("추천 모델 그대로", "recommended"), ("각 CLI 기본 모델 (지정 안 함)", "default"),
                       ("에이전트별로 하나씩", "agent"), ("역할마다 고르기 (Enter = 추천)", "each")])
    if how == "recommended":
        return recommended
    if how == "default":
        return {r["name"]: None for r in roles}
    if how == "agent":
        per = {a: _ask_model(ask, say, f"{a} 역할들의 모델은?", a) for a in dict.fromkeys(r["agent"] for r in roles)}
        return {r["name"]: per[r["agent"]] for r in roles}
    return {r["name"]: _ask_model(ask, say, f"{r['name']} ({r['agent']}) 의 모델은?", r["agent"], recommended[r["name"]])
            for r in roles}


def _collect_preset(ask, say, pid):
    p = PRESETS[pid]
    say(f"\n── {p['label']} ──\n{p['description']}")
    for r in p["roles"]:
        say(f"  - {r['name']} ({r['label']}): 추천 {r['agent']}" + ("  ← 리드" if r["name"] == p["lead"] else ""))
    agents = _ask_preset_agents(ask, say, p["roles"])
    roles = [{"name": r["name"], "label": r["label"], "agent": agents[r["name"]], "command": None, "prompt": r["prompt"]}
             for r in p["roles"]]
    models = _ask_preset_models(ask, say, roles, p["roles"])
    for r in roles:
        r["model"] = models[r["name"]]
    while _ask_yes_no(ask, say, "역할을 더 추가할까요?", default=False):
        roles.append(_ask_role(ask, say, {r["name"] for r in roles}, len(roles) + 1))
    lead, team = _ask_lead_and_team(ask, say, roles, p["lead"], pid)
    return {"roles": roles, "lead": lead, "team": team, "description": p["description"]}


def collect(ask, say, preset=None):
    """Interview the user. Returns {'roles': [...], 'lead': name, 'team': name[, 'description': str]}.

    `preset` (a PRESETS id) skips the first question.
    """
    say("팀 구성을 정해볼게요. (Ctrl+C 로 중단)")
    if preset is None:
        options = [(f"{p['label']} — {p['description']}", pid) for pid, p in PRESETS.items()]
        options.append(("직접 정하기 (역할을 하나씩 입력)", None))
        preset = _ask_choice(ask, say, "어떤 구조로 팀을 만들까요?", options, default=len(options))
    if preset:
        return _collect_preset(ask, say, preset)
    roles = []
    while True:
        roles.append(_ask_role(ask, say, {r["name"] for r in roles}, len(roles) + 1))
        if not _ask_yes_no(ask, say, "역할을 더 추가할까요?", default=len(roles) < 2):
            break
    lead, team = _ask_lead_and_team(ask, say, roles, roles[0]["name"], "custom")
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
        if r.get("model"):
            out.append(f"agent_args = [{_q('--model')}, {_q(r['model'])}]")
        if r["prompt"]:
            out.append(f"prompt = {_q(r['prompt'])}")
        out.append("")
    return "\n".join(out)


def render_team(answers):
    lead = answers["lead"]
    out = ["# `roles setup` 이 만든 팀입니다. split/of 를 생략하면 앞 멤버를 기준으로 자동 배치됩니다.", "",
           "[team]", f"name = {_q(answers['team'])}"]
    if answers.get("description"):
        out.append(f"description = {_q(answers['description'])}")
    out += ["", "[[member]]", f"role = {_q(lead)}", "lead = true", ""]
    for r in answers["roles"]:
        if r["name"] != lead:
            out += ["[[member]]", f"role = {_q(r['name'])}", ""]
    return "\n".join(out)


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def _team_loads(cdir, name):
    try:
        config.load_team(name, config.load_roles(cdir), cdir)
        return True
    except config.ConfigError:
        return False


def _point_default_team(cdir, team):
    """Make `team-up` without --team find the new team, without disturbing other settings.

    Returns the previous default when it was replaced because its team file no longer loads (e.g. `setup --force`
    rewrote roles.toml and that team's roles are gone), else None.
    """
    path = os.path.join(cdir, "config.toml")
    if not os.path.exists(path):
        _write(path, f"[settings]\ndefault_team = {_q(team)}\n")
        return None
    current = config.load_settings(cdir)["default_team"]
    if current and _team_loads(cdir, current):
        return None                 # still a usable team with the new roles: leave the user's choice alone
    replaced = current if current and os.path.exists(os.path.join(cdir, "teams", f"{current}.toml")) else None
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
    return replaced


def _terminal_input():
    """`input` for the real terminal.

    Without line editing the tty erases one *byte* per backspace, so erasing a 한글 syllable can leave half of its
    UTF-8 bytes behind and `input` raises UnicodeDecodeError. readline erases whole characters; decoding with
    errors="replace" turns whatever still slips through into U+FFFD, which `_reask_garbled` catches.
    """
    try:
        import readline  # noqa: F401  (importing it is what makes `input` use it)
    except ImportError:
        pass
    try:
        sys.stdin.reconfigure(errors="replace")
    except (AttributeError, ValueError):
        pass
    return input


def _reask_garbled(ask, say):
    def asker(prompt):
        while True:
            raw = ask(prompt)
            if "�" not in raw:
                return raw
            say("입력에 깨진 글자가 있어요 (한글을 지우다 일부만 지워진 것 같아요). 다시 입력해 주세요.")
    return asker


def run_wizard(ask=None, say=None, cdir=None, preset=None):
    """Interview, confirm, write. Returns the new team's name; raises SetupAborted if the user declines."""
    say = say or print                           # looked up at call time, not bound at import
    ask = _reask_garbled(ask or _terminal_input(), say)
    cdir = cdir or config.config_dir()
    answers = collect(ask, say, preset)
    team_path = os.path.join(cdir, "teams", f"{answers['team']}.toml")
    say("\n── 요약 ──")
    for r in answers["roles"]:
        how = r["agent"] or (f"명령 `{r['command']}`" if r["command"] else "셸")
        if r.get("model"):
            how += f" ({r['model']})"
        say(f"  - {r['name']} ({r['label']}): {how}" + ("  ← 리드" if r["name"] == answers["lead"] else ""))
    if os.path.exists(team_path):
        say(f"  ! 팀 '{answers['team']}' 파일이 이미 있어 덮어씁니다.")
    if os.path.exists(os.path.join(cdir, "roles.toml")):
        say("  ! 기존 roles.toml 을 덮어씁니다.")
    if not _ask_yes_no(ask, say, "이대로 저장할까요?", default=True):
        raise SetupAborted("저장하지 않고 중단했습니다.")
    _write(os.path.join(cdir, "roles.toml"), render_roles(answers["roles"]))
    _write(team_path, render_team(answers))
    replaced = _point_default_team(cdir, answers["team"])
    say(f"저장했어요: {cdir}")
    if replaced:
        say(f"기본 팀을 '{replaced}' → '{answers['team']}' 로 바꿨어요. '{replaced}' 팀은 새 역할로 구성할 수 없어요"
            f" (필요 없으면 teams/{replaced}.toml 을 지우세요).")
    return answers["team"]
