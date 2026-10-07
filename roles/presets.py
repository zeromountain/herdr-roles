"""Team presets modelled on how well-known companies organise product teams.

They are starting points for the setup wizard, not org charts: each one maps a company's team shape onto
agent roles. Each role carries a recommended agent (planning/review/analysis -> claude, writing code -> codex);
the wizard lets the user keep it, pick per role, or use one agent for all. The written files can be edited freely.
Role names must match setup.NAME_RE; the preset id doubles as the default team name.
"""
import json
import os

AGENTS = ("claude", "codex")
CLAUDE_MODELS = ("fable", "opus", "sonnet")     # aliases `claude --model` documents for the latest models


# Recommended `--model` per role tier, one per agent so the recommendation follows the role if its agent is changed.
# Sources (checked 2026-10): platform.claude.com/docs/en/about-claude/models/overview and the codex CLI's own
# model catalog descriptions (~/.codex/models_cache.json).
#   claude fable  "For demanding reasoning and long-horizon agentic work" (slowest, $10/$50 per MTok)
#   claude opus   "For long-running agentic coding and knowledge work"; the docs' default starting point ($4/$20)
#   claude sonnet "The best combination of speed and intelligence" ($2/$10)
#   (claude haiku 4.5 is left out: retirement not sooner than 2026-10-15)
#   codex gpt-6-astra  "Frontier intelligence for the most demanding work."
#   codex gpt-6.1-sol  "Latest workhorse model for coding and everyday work."
#   codex gpt-6-luna   "Fast and affordable model for easier tasks."
LEAD = {"claude": "fable", "codex": "gpt-6-astra"}     # owns the problem: framing, priorities, splitting work
RIGOR = {"claude": "opus", "codex": "gpt-6-astra"}     # mistakes are costly: API/data contracts, code review
CORE = {"claude": "opus", "codex": "gpt-6.1-sol"}      # everyday building: UI, specs, tests, analysis
LIGHT = {"claude": "sonnet", "codex": "gpt-6-luna"}    # short, checklist-like turns: process feedback, ops checks


def recommended_model(role, agent):
    """The preset's model for `role` under `agent`, or None (CLI default) when there is none or codex doesn't list it."""
    model = role.get("models", {}).get(agent)
    if agent == "codex" and model not in _codex_models():
        return None                 # codex model names churn; never write one the local CLI doesn't offer
    return model


def model_choices(agent):
    """`--model` values to offer for an agent. Empty when none are known (the wizard still allows typing one)."""
    if agent == "claude":
        return list(CLAUDE_MODELS)
    if agent == "codex":
        return _codex_models()
    return []


def _codex_models():
    """Models the local codex CLI lists (from its own catalog cache), best first; [] if it has not fetched one."""
    path = os.path.join(os.environ.get("CODEX_HOME") or os.path.expanduser("~/.codex"), "models_cache.json")
    try:
        with open(path, encoding="utf-8") as f:
            models = json.load(f).get("models", [])
        listed = [m for m in models if m.get("visibility") == "list" and m.get("slug")]
        return [m["slug"] for m in sorted(listed, key=lambda m: m.get("priority", 0))]
    except (OSError, ValueError, AttributeError, TypeError):
        return []

HANDOFF = "다른 역할에 넘길 결과는 <<<HANDOFF ... >>> 블록 안에 넣으세요."

PRESETS = {
    "toss-silo": {
        "label": "토스 사일로",
        "description": "PO가 이끄는 작은 목적 조직. 한 사일로가 기획부터 배포·지표까지 스스로 결정합니다.",
        "lead": "po",
        "roles": [
            {"name": "po", "label": "PO", "agent": "claude", "models": LEAD,
             "prompt": "사일로의 PO입니다. 문제 정의, 우선순위, 성공 지표를 정하고 작업을 쪼개 각 역할에 나눠 주세요. "
                       "코드는 직접 수정하지 마세요. " + HANDOFF},
            {"name": "designer", "label": "Product Designer", "agent": "claude", "models": CORE,
             "prompt": "프로덕트 디자이너입니다. 사용자 흐름과 화면 상태(빈 화면·오류·로딩)를 정의하고 UI 명세를 작성하세요. "
                       + HANDOFF},
            {"name": "frontend", "label": "Frontend", "agent": "codex", "models": CORE,
             "prompt": "프론트엔드 개발자입니다. 받은 명세대로 화면을 구현하고 변경 요약을 남기세요. " + HANDOFF},
            {"name": "server", "label": "Server", "agent": "codex", "models": RIGOR,
             "prompt": "서버 개발자입니다. API·데이터 모델을 설계·구현하고 프론트엔드와 맞출 계약을 명시하세요. " + HANDOFF},
            {"name": "analyst", "label": "Data Analyst", "agent": "claude", "models": CORE,
             "prompt": "데이터 분석가입니다. PO가 정한 지표를 측정할 이벤트 설계와 분석 쿼리를 작성하고, 결과를 해석하세요."},
        ],
    },
    "daangn-squad": {
        "label": "당근 스쿼드",
        "description": "하나의 문제 영역을 끝까지 맡는 교차 기능 팀. PM·디자이너·클라이언트·서버·데이터가 한 팀입니다.",
        "lead": "pm",
        "roles": [
            {"name": "pm", "label": "PM", "agent": "claude", "models": LEAD,
             "prompt": "스쿼드의 PM입니다. 사용자 문제와 가설을 정리하고 실험 단위로 작업을 쪼개 나눠 주세요. "
                       "코드는 직접 수정하지 마세요. " + HANDOFF},
            {"name": "designer", "label": "Product Designer", "agent": "claude", "models": CORE,
             "prompt": "프로덕트 디자이너입니다. 가설을 검증할 사용자 흐름과 화면을 설계하고 명세로 남기세요. " + HANDOFF},
            {"name": "client", "label": "Client (iOS/Android/Web)", "agent": "codex", "models": CORE,
             "prompt": "클라이언트 개발자입니다. 명세대로 앱/웹 화면을 구현하고 변경 요약을 남기세요. " + HANDOFF},
            {"name": "backend", "label": "Backend", "agent": "codex", "models": RIGOR,
             "prompt": "백엔드 개발자입니다. 필요한 API와 데이터 처리를 구현하고 클라이언트와 맞출 계약을 명시하세요. "
                       + HANDOFF},
            {"name": "data", "label": "Data", "agent": "claude", "models": CORE,
             "prompt": "데이터 담당입니다. 실험 지표와 로그 설계를 정하고, 결과를 분석해 다음 가설을 제안하세요."},
        ],
    },
    "baemin-tf": {
        "label": "배민 TF",
        "description": "특정 미션을 위해 모였다가 끝나면 흩어지는 태스크포스. 리드가 범위·일정을 쥐고 빠르게 실행합니다.",
        "lead": "tf-lead",
        "roles": [
            {"name": "tf-lead", "label": "TF Lead", "agent": "claude", "models": LEAD,
             "prompt": "TF 리드입니다. 미션의 범위·마감·완료 조건을 정하고, 매 단계 진행 상황을 점검하며 막힌 것을 풀어 주세요. "
                       "코드는 직접 수정하지 마세요. " + HANDOFF},
            {"name": "builder", "label": "Builder", "agent": "codex", "models": CORE,
             "prompt": "TF 개발자입니다. 리드가 정한 범위 안에서 가장 빠른 경로로 구현하고 변경 요약을 남기세요. " + HANDOFF},
            {"name": "qa", "label": "QA", "agent": "claude", "models": CORE,
             "prompt": "QA 담당입니다. 완료 조건 기준으로 테스트를 작성·실행하고, 결함은 재현 절차와 함께 보고하세요. "
                       + HANDOFF},
            {"name": "ops", "label": "Ops", "agent": "claude", "models": LIGHT,
             "prompt": "운영 담당입니다. 배포·모니터링·롤백 계획을 준비하고 출시 후 이상 징후를 확인하세요."},
        ],
    },
    "spotify-squad": {
        "label": "Spotify 스쿼드",
        "description": "자율적인 스쿼드. PO가 '무엇을', 애자일 코치가 '어떻게 일할지'를, 엔지니어가 구현을 맡습니다.",
        "lead": "po",
        "roles": [
            {"name": "po", "label": "Product Owner", "agent": "claude", "models": LEAD,
             "prompt": "스쿼드의 PO입니다. 백로그 우선순위와 완료 조건을 정하고 작업을 나눠 주세요. "
                       "코드는 직접 수정하지 마세요. " + HANDOFF},
            {"name": "coach", "label": "Agile Coach", "agent": "claude", "models": LIGHT,
             "prompt": "애자일 코치입니다. 팀의 작업 흐름을 점검하고 병목·누락된 합의를 짚어 개선안을 제안하세요. "
                       "코드는 직접 수정하지 마세요."},
            {"name": "engineer", "label": "Engineer", "agent": "codex", "models": CORE,
             "prompt": "스쿼드 엔지니어입니다. 받은 작업을 구현하고 변경 요약을 남기세요. " + HANDOFF},
            {"name": "reviewer", "label": "Engineer (Review)", "agent": "claude", "models": RIGOR,
             "prompt": "같은 챕터의 엔지니어로서 리뷰를 맡습니다. 받은 diff만 검토하고, 수정이 필요하면 첫 줄에 "
                       "`CHANGES` 라고 쓰고 항목을 나열, 문제가 없으면 `APPROVED` 라고 쓰세요."},
        ],
    },
    "amazon-two-pizza": {
        "label": "Amazon 투 피자 팀",
        "description": "피자 두 판으로 식사할 수 있는 작은 팀. 한 사람의 오너(single-threaded owner)가 서비스 하나를 끝까지 책임집니다.",
        "lead": "owner",
        "roles": [
            {"name": "owner", "label": "Single-threaded Owner", "agent": "claude", "models": LEAD,
             "prompt": "이 서비스의 단일 책임자입니다. 고객 관점에서 거꾸로(working backwards) 요구사항을 정리하고 "
                       "작업을 나눠 주세요. 코드는 직접 수정하지 마세요. " + HANDOFF},
            {"name": "sde", "label": "SDE", "agent": "codex", "models": CORE,
             "prompt": "개발자입니다. 받은 요구사항을 구현하고 변경 요약을 남기세요. " + HANDOFF},
            {"name": "reviewer", "label": "SDE (Review)", "agent": "claude", "models": RIGOR,
             "prompt": "리뷰 담당 개발자입니다. 받은 diff만 검토하고, 수정이 필요하면 첫 줄에 `CHANGES` 라고 쓰고 "
                       "항목을 나열, 문제가 없으면 `APPROVED` 라고 쓰세요."},
            {"name": "oncall", "label": "On-call", "agent": "claude", "models": LIGHT,
             "prompt": "운영(온콜) 담당입니다. 직접 만든 서비스는 직접 운영한다는 원칙으로 테스트·배포·모니터링을 확인하세요."},
        ],
    },
}
