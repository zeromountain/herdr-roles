# herdr-roles

herdr pane에 **역할**을 부여하고, 메인 세션에서 **팀을 구성**(나머지 역할 pane을 자동 스폰)하며,
역할 사이에서 **작업을 인계**하는 [herdr](https://herdr.dev) 플러그인입니다.

```
메인 pane(Planner, lead) ──team-up──▶  Implementer · Reviewer · Tester · Runner pane 4개 스폰
                          ◀─ send / read ─▶  역할 이름으로 지시하고 결과를 읽음
                          plan → build → review ⇄ fix   (done 감지 → 다음 역할로 인계)
```

개발팀 5역할을 지정하면 **메인 pane이 lead 1개를 맡고 나머지 4개만 새로 스폰**합니다.

## 시작하기

처음 한 번은 아래 3단계를 따라 하면 됩니다.

**준비물**: herdr 0.9.1+, Python 3.11+ (표준 라이브러리만 사용), 팀에 쓸 에이전트 CLI(`claude`, `codex`)가 설치·로그인된 상태.

### 1단계. 플러그인 설치

```sh
herdr plugin install zeromountain/herdr-roles
herdr plugin list          # "herdr-roles ... enabled" 가 보이면 성공
```

> 플러그인은 herdr 전체(모든 세션)에 적용되지만, 팀을 만들지 않은 workspace에서는 아무 일도 하지 않습니다.

### 2단계. 팀 정하기 (설정 마법사)

처음에는 `roles` 명령이 아직 등록되지 않았으므로, 설치된 폴더의 `bin/roles`를 경로째 실행합니다.

```sh
python3 ~/.config/herdr/plugins/github/herdr-roles-*/bin/roles setup
```

질문에 번호로 답하면 됩니다. **모르겠으면 Enter**를 누르세요. 모든 질문의 기본값이 추천값입니다.
아래는 첫 질문에서 `1`(토스 사일로)을 고르고 나머지는 Enter만 누른 예입니다.

```
어떤 구조로 팀을 만들까요?
  1. 토스 사일로 — PO가 이끄는 작은 목적 조직. ...
  2. 당근 스쿼드 — 하나의 문제 영역을 끝까지 맡는 교차 기능 팀. ...
  3. 배민 TF — 특정 미션을 위해 모였다가 끝나면 흩어지는 태스크포스. ...
  4. Spotify 스쿼드 — ...
  5. Amazon 투 피자 팀 — ...
  6. 직접 정하기 (역할을 하나씩 입력)
번호 [6]: 1                                   ← 회사 팀 구조(프리셋)를 고르면 역할이 미리 채워집니다
역할별 에이전트를 어떻게 정할까요?  번호 [1]:    ← Enter: 기획·리뷰는 claude, 구현은 codex
모델은 어떻게 정할까요?  번호 [1]:              ← Enter: 역할에 맞는 추천 모델
역할을 더 추가할까요? (y/N):                   ← Enter: 추가 안 함
지금 pane(team-up 을 실행한 곳)이 맡을 리드 역할은?  번호 [1]:   ← Enter: po
팀 이름 [toss-silo]:
── 요약 ──
  - po (PO): claude (fable)  ← 리드
  - designer (Product Designer): claude (opus)
  - frontend (Frontend): codex (gpt-6.1-sol)
  - server (Server): codex (gpt-6-astra)
  - analyst (Data Analyst): claude (opus)
이대로 저장할까요? (Y/n):
저장했어요: ~/.config/herdr/plugins/config/herdr-roles

에이전트에서 쓸 수 있게 연결:
  ✓ roles 명령: ~/.local/bin/roles 연결했어요
  ✓ Claude Code 스킬: ~/.claude/skills/herdr-roles 연결했어요
  ✓ Codex 스킬: ~/.codex/skills/herdr-roles 연결했어요

팀 'toss-silo' 준비 완료. 리드로 쓸 pane 에서 `roles team-up` 을 실행하세요.
```

마법사는 저장한 뒤 두 가지를 자동으로 연결합니다(`roles install`과 같습니다). 이후로는 `roles`만 치면 됩니다.
- **`roles` 명령**: `~/.local/bin/roles` → 플러그인의 `bin/roles`. `~/.local/bin`이 PATH에 없으면 추가하는 명령을 알려 줍니다.
- **에이전트 스킬**: `~/.claude/skills/herdr-roles`, `~/.codex/skills/herdr-roles` → 플러그인의 `skills/herdr-roles`.
  Claude Code·Codex가 "팀 구성해줘", "`roles team-up`" 같은 말을 이 셸 명령으로 알아듣게 합니다. 설치된 에이전트에만 연결합니다.

모두 플러그인 폴더를 가리키는 링크라서, 플러그인을 다시 설치하면 함께 최신이 됩니다. 이미 있는 파일은 덮어쓰지 않고,
다른 곳(예: 로컬 클론)을 가리키는 링크는 그대로 둡니다. 바꾸려면 `roles install --force`.

- 다시 정하고 싶으면 `roles setup --force` (기존 역할 설정을 덮어씁니다).
- 각 질문의 선택지와 프리셋·추천 모델의 근거는 [설정 마법사 자세히](#설정-마법사-자세히)에 있습니다.

<details>
<summary>터미널 대신 다른 방법으로 정하기</summary>

- **herdr 팝업으로**: herdr 안에서 아래를 실행하면 마법사가 팝업으로 뜹니다. 끝나면 똑같이 명령과 스킬을 연결합니다.
  ```sh
  herdr plugin pane open --plugin herdr-roles --entrypoint setup
  ```
  역할이 하나도 없을 때 팀 구성(`team-up`)을 실행해도 같은 팝업이 먼저 뜹니다.
- **Claude Code 같은 에이전트에게 맡길 때**: 에이전트는 질문에 답할 수 없어 마법사가 첫 질문에서 끝납니다.
  프리셋을 정해 모든 질문을 추천값으로 넘기게 하세요. 프리셋 목록은 `roles setup --list-presets`로 볼 수 있습니다.
  ```sh
  yes '' | roles setup --preset toss-silo
  ```
- **예제를 복사해서 직접 고치기**: `roles init`이 개발팀 5역할 예제를 설정 폴더에 복사합니다.
  설정 폴더 위치는 `herdr plugin config-dir herdr-roles`로 확인하고, 파일 형식은 [설정](#설정-configherdrpluginsconfigherdr-roles)을 보세요.
  이 방법은 마법사를 거치지 않으므로 `roles install`을 따로 한 번 실행하세요.
- **로컬 클론을 쓸 때**: `herdr plugin link <클론 경로>` 뒤 `<클론 경로>/bin/roles install --force`로 링크를 클론으로 돌립니다.
</details>

### 3단계. 팀 띄우고 일 시키기

**리드로 쓸 pane**에서 `team-up`을 실행합니다. 그 pane이 리드가 되고, 나머지 역할은 새 pane으로 스폰되어 에이전트가 뜹니다.

- 리드 pane에서 Claude Code나 Codex가 돌고 있다면, 그 에이전트에게 이렇게 말하면 됩니다.
  > 팀 구성해줘  (또는 `roles team-up` 실행해줘)

  스킬은 에이전트가 시작할 때 읽히므로, 2단계 전부터 열려 있던 에이전트는 **새로 시작**하세요.
- 셸 pane이라면 직접 실행합니다.
  ```sh
  roles team-up --dry-run    # 무엇을 띄울지 미리 보기 (아무것도 만들지 않음)
  roles team-up              # 팀 구성
  ```

팀이 뜨면 리드 에이전트는 팀원 목록과 사용법을 프롬프트로 받습니다. 그래서 리드에게 평소처럼 일을 맡기면
리드가 알아서 팀원에게 나눠 줍니다. 직접 시키고 싶다면:

```sh
roles send --role server --text "로그인 API 만들어줘"   # 역할 이름으로 지시
roles read --role server                               # 그 역할의 최근 출력 읽기
roles status                                           # 팀 상태
roles team-down                                        # 다 쓰면 정리 (스폰한 pane만 닫음)
```

정해 둔 순서대로 역할을 거치게 하려면 [워크플로우](#워크플로우)를 쓰세요 (`roles run --workflow <이름> --input "요청"`).

### 보고와 팀원끼리 소통

- **자동 보고**: 리드나 팀원이 `send`로 일을 맡기면 누가 보냈는지가 함께 전달됩니다. 맡은 쪽 에이전트가 일을 끝내면
  (`working` → `idle`/`done`) 그 결과를 보낸 쪽 pane에 자동으로 보고합니다. 출력에 `<<<HANDOFF ... >>>` 블록이 있으면
  그 블록만 보내고, 없으면 마지막 출력을 줄여서 보냅니다. 받는 쪽이 작업 중이면 끝날 때까지 기다렸다가 전달하므로 하던 일이 끊기지 않습니다.
- **팀원끼리 직접 요청**: 팀원은 처음에 팀원 목록과 연락 방법을 안내받습니다. 그래서 다른 역할에게 직접
  `roles send --role <역할>`로 묻거나 일을 넘깁니다. 답은 요청한 팀원에게 가고, 리드에게는
  "참조: A → B: …", "참조: B 가 A 의 요청을 마쳤습니다: …" 같은 한 줄 참조가 갑니다.
- 내 셸(팀에 속하지 않은 pane)에서 보낸 `send`와 워크플로우가 진행 중인 단계는 추적하지 않습니다.
  워크플로우 단계는 워크플로우 인계가 담당합니다.

### 여러 팀을 병렬로 (fleet)

한 pane(오케스트레이터)에서 팀을 여러 개 띄워 동시에 일을 맡길 수 있습니다. 팀마다 **새 workspace**가 열리고,
그 workspace의 첫 pane이 리드가 되어 리드 에이전트와 팀원이 뜹니다. 오케스트레이터 pane은 팀에 속하지 않고 지휘만 합니다.

오케스트레이터로 쓸 pane의 에이전트에게 이렇게 말하면 됩니다.
> auth 팀은 로그인 기능, billing 팀은 결제 버그 수정을 병렬로 맡겨줘

직접 실행한다면:

```sh
roles fleet-up auth billing --task "로그인 기능 추가" --task "결제 금액 반올림 버그 수정"
roles fleet-up docs=writers --task "API 문서 정리"   # 이름=팀설정 으로 팀 구성을 팀마다 지정 (기본: default_team)
roles fleet-up auth billing --worktree ...          # 팀마다 git worktree(브랜치 roles/<이름>)에서 작업
roles fleet                                         # 팀별 상태: 리드 상태, 일하는 팀원 수, 받은 보고 수
roles send --fleet auth --text "로그아웃도 추가해줘"   # 그 팀 리드에게 새 요청
roles read --fleet auth                             # 그 팀 리드의 최근 출력
roles fleet-down auth                               # 그 팀의 workspace 닫기 (이름 없으면 전부)
```

- **보고**: 리드는 받은 일을 팀원에게 나눠 맡기고, 팀의 일이 **모두** 끝나면 `<<<HANDOFF ... >>>` 블록으로 결과를 요약합니다.
  그 블록이 오케스트레이터 pane에 `[herdr-roles] 팀 'auth' ... 작업 완료 보고`로 자동 전달됩니다. 리드가 팀원에게 일을 넘기고
  턴을 마치는 것은 보고로 치지 않습니다(요청 때 화면에 없던 **새** HANDOFF 블록만 보고). 리드가 블록을 빠뜨려도, 팀원에게 맡긴
  요청이 모두 끝나고 일하는 팀원이 없을 때 리드가 쉬면 그 마지막 출력을 보고합니다. 오케스트레이터가 작업 중이면 끝난 뒤 전달합니다.
- **병렬의 범위**: 팀을 띄우는 일은 한 팀씩 차례로 진행되고(팀원마다 에이전트 기동에 몇 초), 첫 팀은 띄워지는 즉시
  일을 시작하므로 **작업은 팀끼리 동시에** 진행됩니다.
- **같은 저장소를 여러 팀이**: 기본은 모든 팀이 오케스트레이터와 같은 폴더에서 일하므로 같은 파일을 고치면 서로 충돌할 수 있습니다.
  겹치는 작업이면 `--worktree`를 쓰세요. 단, worktree 폴더(`~/.herdr/worktrees/…`)는 처음 보는 폴더라 Claude·Codex가 신뢰 질문을 합니다.
  `trust_folders = true`로 미리 등록할 수 있습니다([알아 둘 점](#알아-둘-점)의 trust 프롬프트 참고). `fleet-down`은 worktree 폴더와 브랜치를 지우지 않습니다.
- 같은 이름으로 `fleet-up`을 다시 실행하면 새로 만들지 않고 부족한 팀원만 채운 뒤 `--task`를 새 요청으로 보냅니다(멱등).
- 리드 역할은 에이전트여야 합니다(셸 역할이면 요청을 받을 수 없어 거부합니다).
- 팀을 띄우는 동안에는 상태 잠금을 잡고 있어서, 이미 일하고 있는 다른 팀의 상태 이벤트가 늦게 처리되거나 건너뛰어질 수 있습니다.
  보고는 HANDOFF 블록과 `idle_grace_s` 기준으로 판단하므로 빠지지 않지만, 팀원→리드 보고가 몇 초 늦을 수 있습니다.

## 설정 마법사 자세히

```sh
roles setup                          # 역할이 없을 때만 실행됨
roles setup --force                  # 이미 있어도 다시 정함 (roles.toml 덮어씀)
roles setup --list-presets           # 기업 팀 구조 프리셋 목록
roles setup --preset toss-silo       # 프리셋으로 바로 시작 (첫 질문 생략)
```

### 기업 팀 구조 프리셋

첫 질문 "어떤 구조로 팀을 만들까요?"에서 잘 알려진 회사의 팀 구조를 골라 시작할 수 있습니다.
조직도를 그대로 옮긴 것이 아니라, 그 팀 구조의 역할 분담을 에이전트 역할로 옮긴 출발점입니다.

| id | 구조 | 리드 | 역할 (추천 에이전트) |
|---|---|---|---|
| `toss-silo` | 토스 사일로: PO가 이끄는 작은 목적 조직 | `po` | po·designer·analyst = claude, frontend·server = codex |
| `daangn-squad` | 당근 스쿼드: 하나의 문제 영역을 끝까지 맡는 교차 기능 팀 | `pm` | pm·designer·data = claude, client·backend = codex |
| `baemin-tf` | 배민 TF: 미션 단위로 모였다 흩어지는 태스크포스 | `tf-lead` | tf-lead·qa·ops = claude, builder = codex |
| `spotify-squad` | Spotify 스쿼드: PO + 애자일 코치 + 엔지니어 | `po` | po·coach·reviewer = claude, engineer = codex |
| `amazon-two-pizza` | Amazon 투 피자 팀: 단일 책임 오너가 서비스 하나를 책임 | `owner` | owner·reviewer·oncall = claude, sde = codex |

프리셋을 고르면 역할마다 담당 업무 `prompt`가 미리 채워지고, 아래 항목만 묻습니다.
- **역할별 에이전트** (claude / codex). 기획·리뷰·분석 역할은 claude, 코드를 쓰는 역할은 codex가 추천값입니다.
  1. 추천 구성 그대로 (기본값)
  2. 역할마다 고르기: 역할별로 claude/codex를 묻고, Enter는 그 역할의 추천값입니다
  3. 모두 claude / 4. 모두 codex
- **모델**: 1. 추천 모델 그대로(기본값) / 2. 각 CLI 기본 모델(지정 안 함) / 3. 에이전트별로 하나씩 / 4. 역할마다 고르기(Enter = 추천)
  - 추천 모델은 역할의 성격을 4단계로 나눠 정했습니다. 역할마다 claude용·codex용이 따로 있어, 에이전트를 바꿔도 추천이 따라갑니다.

    | 단계 | 역할 | claude | codex | 근거 |
    |---|---|---|---|---|
    | LEAD | po, pm, tf-lead, owner | `fable` | `gpt-6-astra` | 문제 정의·우선순위·작업 분해는 고난도 추론·장시간 작업 |
    | RIGOR | server, backend, reviewer | `opus` | `gpt-6-astra` | API·데이터 계약, 코드 리뷰는 실수 비용이 큼 |
    | CORE | designer, frontend, client, builder, engineer, sde, analyst, data, qa | `opus` | `gpt-6.1-sol` | 일상적인 구현·명세·테스트·분석 |
    | LIGHT | coach, ops, oncall | `sonnet` | `gpt-6-luna` | 짧고 체크리스트형인 작업 |

    근거 자료(2026-10 확인): Anthropic [모델 개요](https://platform.claude.com/docs/en/about-claude/models/overview)
    (fable "demanding reasoning and long-horizon agentic work", opus "long-running agentic coding and knowledge work" · 대부분 작업의 출발점,
    sonnet "best combination of speed and intelligence"; haiku 4.5는 2026-10-15 이후 퇴역 가능해 제외)와
    codex CLI 모델 카탈로그의 설명(astra "most demanding work", 6.1-sol "workhorse model for coding", luna "easier tasks").
    추천값을 바꾸려면 `roles/presets.py`의 `LEAD`/`RIGOR`/`CORE`/`LIGHT`를 고치세요.
  - codex 추천 모델이 로컬 카탈로그에 없으면(이름 변경, 캐시 없음) 그 역할은 codex 기본 모델을 쓰고 마법사가 알려 줍니다.
  - claude 선택지는 `claude --model` 별칭 `fable`, `opus`, `sonnet`입니다.
  - codex 선택지는 로컬 codex CLI의 모델 카탈로그(`$CODEX_HOME/models_cache.json`, 기본 `~/.codex`)에서 노출된 모델을 읽습니다.
    캐시가 없으면(codex를 한 번도 실행하지 않은 경우) "기본값"과 "직접 입력"만 나옵니다.
  - 고른 모델은 `roles.toml`에 `agent_args = ["--model", "<모델>"]`로 저장되어 `herdr agent start ... -- --model <모델>`로 전달됩니다.
  - 리드 pane은 `team-up`을 실행한 곳에서 이미 돌고 있는 에이전트를 그대로 씁니다. 리드 역할에 고른 에이전트·모델은 적용되지 않고, 스폰되는 팀원에만 적용됩니다.
- **역할 추가 여부** (기본 "아니오"). 예라고 답하면 아래의 직접 정하기 질문으로 역할을 덧붙입니다
- **리드 역할**: 기본값은 프리셋의 리드
- **팀 이름**: 기본값은 프리셋 id. 팀 파일의 `description`에는 프리셋 설명이 들어갑니다

프리셋을 추가하거나 고치려면 `roles/presets.py`를 편집하세요.

### 직접 정하기

첫 질문에서 Enter(마지막 항목 "직접 정하기")를 누르면 역할을 하나씩 정합니다. 묻는 순서:
1. **역할** (하나씩 반복): 이름(영문 소문자·숫자·`-`·`_`), 화면에 보일 이름, 실행 방식을 묻습니다.
   - AI 에이전트: claude, codex, gemini, opencode 중 선택하거나 `--kind` 값을 직접 입력하고, 담당 업무를 한두 문장으로 적습니다. 이 문장이 역할 `prompt`가 되고 리드의 로스터에도 표시됩니다.
   - 셸 명령: `pnpm dev`처럼 pane에서 실행할 명령을 적습니다.
   - 그냥 셸: 아무것도 실행하지 않는 pane입니다.
2. **역할 추가 여부**: 기본값은 2개가 될 때까지 "예", 그 뒤로는 "아니오"입니다.
3. **리드 역할**: `team-up`을 실행한 pane이 맡을 역할입니다. 역할이 하나면 묻지 않습니다.
4. **팀 이름**: 기본값은 `custom`입니다.
5. **요약 확인**: "이대로 저장할까요?"에 아니오로 답하면 아무것도 저장하지 않습니다.

저장하는 파일(설정 디렉터리 아래):

| 파일 | 내용 |
|---|---|
| `roles.toml` | 답한 역할 전부. **기존 파일을 덮어씁니다** |
| `teams/<팀 이름>.toml` | 리드 + 나머지 역할. `split`/`of` 없이 저장되며 앞 멤버를 기준으로 자동 배치됩니다 |
| `config.toml` | `default_team`이 없거나, 그 팀이 없거나, 새 역할로는 구성할 수 없을 때(예: `--force`로 역할을 바꿔 기존 팀의 역할이 사라짐)만 새 팀으로 설정하고 바꿨다고 알려 줍니다. 다른 설정은 건드리지 않습니다 |

자동 실행:
- 역할이 하나도 없을 때(`roles.toml`이 없거나 `[roles.*]`가 비어 있을 때) `team-up`과 `picker` 메뉴가 마법사를 먼저 실행합니다.
  `team-up`은 마법사가 만든 팀으로 바로 이어서 팀을 구성합니다.
- 터미널이 없는 곳(herdr 메뉴 액션, 에이전트가 실행한 셸)에서는 `setup` 팝업 pane을 열고 종료합니다.
  팝업에서 답한 뒤 `team-up`을 다시 실행하세요.
- `roles.toml` 문법이 깨진 경우에는 마법사를 띄우지 않고 오류를 보여 줍니다(덮어써서 고치지 않습니다).

마법사가 만든 파일은 그대로 고쳐도 됩니다. 배치(`split`/`of`/`ratio`), 워크플로우 등 세부 항목은 아래 [설정](#설정-configherdrpluginsconfigherdr-roles)을 보세요.

## 명령 모음

```sh
roles team-up --dry-run          # 무엇을 스폰할지 미리 보기
roles team-up --team dev         # 이 pane = lead, 나머지 역할 pane 스폰 + 에이전트 기동 + 로스터 주입
roles status                     # 팀/워크플로우 상태
roles send --role reviewer --text "auth.py 리뷰해줘"
roles read --role reviewer --lines 200
roles run --workflow feature --input "로그인 기능 추가"
roles advance                    # 반자동 단계에서 다음 역할로 인계
roles team-down                  # 스폰한 pane만 정리 (lead·직접 만든 pane은 유지)
roles install [--force]          # roles 명령·에이전트 스킬 연결 (setup 이 자동 실행)
roles setup --scope project      # 이 프로젝트 전용 팀을 .herdr-roles/ 에 (커밋해 공유. --project 와 같음)
roles setup --scope user         # 이 프로젝트에서 나만 쓰는 설정을 .herdr-roles/local/ 에 (git 제외)
roles config                     # 전역·프로젝트·사용자 계층과 각자 정의한 역할·팀·설정 보기
roles fleet-up a b --task "…" --task "…"   # 이 pane = 오케스트레이터, 팀마다 새 workspace 로 병렬 팀 (fleet 참고)
roles fleet / fleet-down [이름]            # fleet 팀 상태 / 닫기
roles send --fleet a --text "…"           # fleet 팀 리드에게 요청 (read --fleet 도 같음)
```

`--team`을 생략하면 `config.toml`의 `default_team`(마법사가 마지막으로 만든 팀)을 씁니다.

`team-up`은 **멱등**입니다. 부족한 pane만 만들고, 죽은 pane은 다시 만들며, 에이전트가 사라진 멤버는 다시 시작합니다.
이미 정상인 pane은 건드리지 않습니다.

lead가 에이전트(Claude 등)면 로스터(팀원 목록과 `send`/`read` 사용법)를 프롬프트로 받습니다.
그래서 lead에게 "개발팀 구성해줘"라고 시키면 lead가 직접 `roles team-up`을 실행할 수 있습니다.
lead가 `working`이면 로스터 주입을 보류했다가 idle이 될 때 전달합니다.

메뉴에서도 쓸 수 있습니다: 액션 `herdr-roles.assign`(메뉴 팝업), `herdr-roles.team-up`, `herdr-roles.team-down`,
`herdr-roles.status`, `herdr-roles.advance`, `herdr-roles.reapply`. 플러그인 pane: `picker`(메뉴), `board`(상태 대시보드).

> 액션은 **호출한 pane이 아니라 포커스된 pane**을 대상으로 합니다(herdr 액션 컨텍스트의 동작).
> 에이전트 셸에서 쓸 때는 `roles`가 `$HERDR_PANE_ID`(진짜 호출 pane)를 쓰므로 이쪽이 정확합니다.

## 이전 버전에서 업데이트하기

설치된 플러그인은 저절로 업데이트되지 않습니다. 아래 순서로 올리세요. 설정 파일 형식은 그대로라 기존 팀 설정을 계속 씁니다.

1. **플러그인 다시 설치**
   ```sh
   herdr plugin install zeromountain/herdr-roles --yes
   herdr plugin list          # 커밋이 바뀌었는지 확인
   ```
   설치 폴더 이름(`herdr-roles-<id>`)은 그대로라, 이미 있는 `~/.local/bin/roles` 링크는 자동으로 새 버전을 가리킵니다.
2. **명령·스킬 연결**
   ```sh
   roles install
   ```
   `roles: command not found`가 나오면(예전에 링크를 만들지 않은 경우) 경로째 실행하세요:
   `python3 ~/.config/herdr/plugins/github/herdr-roles-*/bin/roles install`. 출력에 PATH 경고가 있으면 안내된 한 줄을 실행하세요.
   0.1.x에서 올라오면 Claude Code·Codex 스킬이 이때 처음 연결됩니다.
3. **에이전트 새로 시작**: Claude Code·Codex는 시작할 때만 스킬을 읽습니다.
4. (선택) **프로젝트 전용 팀**: 필요한 저장소의 터미널에서 `roles setup --scope project`(공유) 또는 `--scope user`(나만). 전역 설정은 그대로 둡니다.

따로 할 필요 없는 것:
- **떠 있는 팀**: `team-down` 하지 않아도 됩니다. 훅은 이벤트마다 `bin/roles`를 새로 실행하므로 재설치 직후부터 새 코드를 씁니다.
  이전 버전에서 만든 팀은 전역 설정 팀으로 다룹니다. 다만 0.3.x 이하에서 띄운 팀원은 팀원 목록과 연락 방법을 안내받지 못했습니다.
  자동 보고는 바로 동작하지만, 팀원끼리 직접 요청하게 하려면 `roles team-down` 뒤 `roles team-up`으로 다시 띄우세요.
- **herdr 재시작**: 액션·훅·pane 등록(`herdr-plugin.toml`)은 버전 번호 말고 바뀌지 않았습니다.
- **Python 설정**: `roles`는 3.11 미만으로 시작되면 새 Python을 찾아 다시 실행합니다. 찾지 못할 때만 오류 안내대로
  `~/.zprofile`에 `brew shellenv`를 추가하세요.

## 문제 해결

| 증상 | 해결 |
|---|---|
| `roles: command not found` | `python3 ~/.config/herdr/plugins/github/herdr-roles-*/bin/roles install`로 다시 연결하세요. 출력에 PATH 경고가 있으면 안내된 한 줄을 실행하고 셸(에이전트)을 새로 여세요. |
| 에이전트에게 `roles team-up`을 시켜도 실행하지 않음 | 스킬이 연결됐는지 `roles install`로 확인하고, 에이전트를 새로 시작하세요(스킬은 시작할 때 읽힙니다). |
| `herdr-roles 는 Python 3.11+ 가 필요한데 ...` | `roles`는 3.11 미만으로 시작되면 더 새 Python(`python3.1x`, Homebrew)을 찾아 다시 실행합니다. 이 오류는 찾지 못한 것입니다. 에이전트·훅의 로그인 셸은 `~/.zshrc`를 읽지 않아 Homebrew 경로를 모를 수 있습니다. 오류에 나온 대로 `~/.zprofile`에 `brew shellenv`를 추가하거나 `ROLES_PYTHON`으로 지정하세요. |
| `error: 입력이 끝나 설정을 중단했습니다` | 질문에 답할 수 없는 곳(에이전트, 파이프)에서 `roles setup`을 실행했습니다. 터미널에서 실행하거나 `yes '' \| roles setup --preset <프리셋>`을 쓰세요. |
| `이미 역할이 설정돼 있습니다` | 이미 팀을 정했습니다. 다시 정하려면 `roles setup --force`. |
| 고쳐졌다는 문제(예: 한글 입력 중 `UnicodeDecodeError`)가 그대로 남음 | 설치된 플러그인은 저절로 업데이트되지 않습니다. [이전 버전에서 업데이트하기](#이전-버전에서-업데이트하기) 순서대로 다시 설치하세요. 버전은 `git -C ~/.config/herdr/plugins/github/herdr-roles-* log --oneline -1`로 확인합니다. |
| 팀원 pane에 에이전트가 안 뜨고 `failed` | Claude의 폴더 신뢰(trust) 질문 때문일 수 있습니다. 아래 [알아 둘 점](#알아-둘-점)을 보세요. |

## 설정 (`~/.config/herdr/plugins/config/herdr-roles/`)

정확한 위치는 `herdr plugin config-dir herdr-roles`가 알려 줍니다. 설정 디렉터리는 아래 순서로 정합니다.
1. `ROLES_CONFIG_DIR` (직접 지정)
2. `HERDR_PLUGIN_CONFIG_DIR`: herdr가 메뉴 액션·훅·플러그인 pane을 실행할 때 넘겨 줍니다
3. `herdr plugin config-dir herdr-roles`: 셸에서 `roles`를 직접 실행할 때 herdr에 물어봅니다
4. `~/.config/herdr/plugins/config/herdr-roles` (herdr가 없거나 답하지 못할 때)

그래서 기기마다 herdr의 설정 위치가 달라도, 메뉴 액션과 셸 명령이 같은 디렉터리를 봅니다.

| 파일 | 내용 |
|---|---|
| `roles.toml` | 역할: `label`, `agent`(= `herdr agent start --kind` 값) 또는 `command`(셸 역할), `prompt`, `agent_args` |
| `teams/*.toml` | 팀: `[[member]]` `role`, `lead`(정확히 1개), `split`/`of`(배치 힌트), `ratio`(선택), `count` |
| `workflows/*.toml` | 흐름: `[[step]]` `id`, `role`, `from`(문자열 또는 목록), `handoff`, `auto`, `template`, `when`, `max_iterations` |
| `config.toml` | `default_team`, `default_workflow`, `max_spawn`(기본 8), `handoff_lines`, `spawn_timeout_ms`, `idle_grace_s` |

`examples/`에 개발팀(5역할)과 `plan → build → review ⇄ fix` 워크플로우가 있습니다. 설정은 실행 전에 전부 검증됩니다
(lead 1개, 정의된 역할, 앞선 멤버만 `of` 참조, 존재하는 `from` 등). 오류가 있으면 pane을 하나도 만들기 전에 실패합니다.

### 설정 계층: 전역 · 프로젝트 · 사용자

설정은 세 계층에 둘 수 있고, 아래 계층일수록 우선합니다. 세 곳 모두 형식(`roles.toml`, `config.toml`, `teams/`, `workflows/`)이 같습니다.

| 계층 | 위치 | 용도 |
|---|---|---|
| 전역 | `~/.config/herdr/plugins/config/herdr-roles/` | 이 PC의 모든 저장소에서 쓰는 기본 역할·팀 |
| 프로젝트 | `<git 루트>/.herdr-roles/` | 이 저장소의 팀 구성. 커밋하면 같은 저장소를 쓰는 사람과 공유 |
| 사용자 | `<git 루트>/.herdr-roles/local/` | 이 저장소에서 **나만** 쓰는 덮어쓰기. 안에 `*` 짜리 `.gitignore` 를 만들어 커밋되지 않음 |

```sh
cd ~/work/my-app
roles setup --scope project   # 마법사 결과를 <git 루트>/.herdr-roles/ 에 (예전 --project 와 같음)
roles setup --scope user      # 마법사 결과를 <git 루트>/.herdr-roles/local/ 에
roles init --scope user       # 또는 예제를 복사해서 직접 고치기 (--scope 는 init 에도 있음)
roles config                  # 지금 폴더에서 적용되는 계층과 각 계층이 정의한 것 보기
```

`--scope` 를 생략하면 전역에 저장합니다. 예를 들어 팀이 커밋한 `.herdr-roles/`는 그대로 두고, 나만 리뷰어를 Codex로 바꾸고
싶다면 `.herdr-roles/local/roles.toml`에 그 역할만 적으면 됩니다.

```
my-app/.herdr-roles/
├── roles.toml          # 이 프로젝트에만 있는 역할, 또는 전역 역할을 같은 이름으로 덮어쓰기
├── config.toml         # 예: default_team = "web"
├── teams/web.toml
├── workflows/…
└── local/              # 사용자 계층 (git 제외)
    ├── .gitignore
    └── roles.toml      # 예: [roles.reviewer] agent = "codex"
```

- **겹쳐 쓰기**: 전역 위에 프로젝트, 그 위에 사용자 설정을 얹습니다. 역할·팀·워크플로우는 이름이 같으면 위 계층 것이 이기고,
  `config.toml`은 항목별로 위 계층 값이 이깁니다. 그래서 프로젝트 팀이 전역 역할(예: `planner`)을 그대로 쓸 수 있고,
  프로젝트에는 바뀌는 것만 두면 됩니다.
- **어느 프로젝트인지**: `team-up`은 리드 pane의 작업 폴더에서 위로 올라가며 가장 가까운 `.herdr-roles/`를 찾습니다.
  없으면 전역 설정만 씁니다. 팀원 pane도 리드와 같은 폴더에서 열립니다.
- **팀이 뜬 뒤**: 그 workspace는 팀을 만든 프로젝트를 기억합니다. `send`/`read`/`status`/`run`과 훅(워크플로우 인계,
  재시작 후 표시 복구)은 지금 폴더와 상관없이 그 프로젝트 설정을 씁니다. 다른 프로젝트로 바꾸려면 그 폴더에서 `team-up`을
  다시 실행하거나 `team-down` 뒤 새로 구성하세요.
- **공유 여부**: `.herdr-roles/`를 커밋하면 같은 저장소를 쓰는 사람과 팀 구성을 공유하고, `.gitignore`에 넣으면 나만 씁니다.
- `ROLES_PROJECT_DIR=<경로>`로 프로젝트 폴더를 직접 지정할 수 있고, 빈 값이면 프로젝트 설정을 쓰지 않습니다.

### 배치
`split = "right"|"down"`, `of = "<앞선 역할>"`로 기준 pane 옆에 붙입니다. 같은 기준에 여러 개를 붙일 때 `ratio`를
생략하면 **균등 분할 비율을 계산**합니다(같은 pane을 같은 비율로 반복 분할하면 높이가 1/3/8/28행으로 무너지는 것을
실측했습니다). `ratio`를 쓰면 "기준 pane이 유지하는 비율"로 그대로 전달됩니다.

### 워크플로우
- `handoff`: `last_output`(pane 출력의 마지막 N줄, `<<<HANDOFF ... >>>` 블록이 있으면 그 블록만) | `git_diff` | `file:<path>` | `none`.
- `when = "output ~ /CHANGES/"`: 이전 단계 출력에 정규식이 있을 때만 진행(`!~`는 부정). 분기는 처음 일치하는 step.
- 기본은 **반자동**: 단계가 끝나면 알림을 보내고 `advance`를 기다립니다. `auto = true`인 단계만 자동 전달합니다.
- 루프 방지: `[workflow].max_hops`, 단계별 `max_iterations`, 그리고 한 run에는 활성 (pane, step)이 하나뿐이라 중복 이벤트가
  두 번 인계되지 않습니다. 작업 pane이 닫히거나 대상 역할 pane이 없으면 run을 `halted`로 두고 알립니다.
- 완료 판정: 에이전트가 `working`을 거친 뒤 `idle`/`done`이 되면 완료입니다(herdr는 완료를 `idle`로 보고하기도 합니다).
  `working`을 못 본 `idle`은 `idle_grace_s` 동안 무시합니다.

## 동작 방식
- 역할은 herdr 밖(`~/.local/state/herdr/plugins/herdr-roles/<세션>/<workspace>.json`)에 저장하고, pane 제목/토큰/상태 라벨은
  거기서 파생한 **표시용 투영**(`pane report-metadata`)입니다. herdr는 재시작 때 메타데이터를 지우므로 `[[startup]]` 훅이 다시 적용합니다.
- 이벤트 훅(`pane.agent_status_changed`, `pane.closed`)이 짧게 실행되는 `dispatch`로 상태를 갱신하고 종료합니다. 상주 프로세스가 없습니다.
  `send`의 요청(`asks`: 누가 누구에게)과 바쁜 에이전트에게 보낼 대기 메시지(`outbox`)도 같은 상태 파일에 두고, 이 훅이 보고·전달합니다.
- 상태 파일은 **세션 이름으로 구분**됩니다(훅은 기본 세션과 이름 있는 세션 모두에서 실행되기 때문).
- fleet: 오케스트레이터 workspace의 상태 파일에 `fleet`(팀 이름 → workspace·리드)을, 각 팀 workspace에 `parent`(오케스트레이터 pane)를
  둡니다. 오케스트레이터의 요청은 리드 pane의 `asks`에 다른 workspace 발신으로 기록되고, 보고는 오케스트레이터 workspace의 `outbox`를 거칩니다.

## 알아 둘 점
- **trust 프롬프트**: Claude와 Codex는 처음 여는 폴더마다 "신뢰하시겠습니까?"를 묻고, 그동안 `agent start`가 `blocked`로 실패합니다.
  해당 역할은 `failed`로 기록되고 나머지 팀은 계속 만들어집니다. 질문에 답한 뒤 `team-up`(fleet 이면 같은 이름으로 `fleet-up`)을
  다시 실행하면 실패한 팀원만 다시 띄웁니다. 에이전트가 이미 떠 있으면 다시 시작하지 않고 팀 안내만 보냅니다.
  **매번 답하기 번거롭다면** `config.toml`의 `[settings]`에 `trust_folders = true`를 넣으세요. 팀을 띄우기 전에 그 폴더를
  Claude Code(`~/.claude.json`)와 Codex(`~/.codex/config.toml`)에 '신뢰'로 미리 등록합니다. 질문에 직접 "신뢰"로 답한 것과 같으니,
  믿을 수 있는 저장소에서만 켜세요. Codex에서 이미 정해 둔 폴더(신뢰하지 않음 포함)는 바꾸지 않습니다.
- **herdr 재시작**: pane과 메타데이터(`[[startup]]` 훅이 재적용)는 복구됩니다. 에이전트는 herdr가 `claude --resume`으로
  되살리려 하지만, 실패하면 pane이 빈 셸로 남습니다(제 테스트 환경에서는 대화 저장이 꺼져 있어 항상 실패했습니다).
  `team-up`을 다시 실행하면 에이전트가 없는 멤버만 다시 띄웁니다. 죽은 에이전트에 대한 `agent prompt`는 오류로 남기며,
  pane에 텍스트를 대신 타이핑하지 않습니다(빈 셸이면 인계 내용이 실행돼 버리기 때문).
- 사용자가 직접 띄운 에이전트(메인 pane의 claude)에도 `agent prompt <pane>`이 동작합니다 — lead 주입과 워크플로우 시작에 `agent start`가 필요 없습니다.
- **비용**: 에이전트 역할 하나당 에이전트가 하나씩 뜹니다. `max_spawn`으로 상한을 두고 `--dry-run`으로 먼저 확인하세요.
- 플러그인은 샌드박스되지 않습니다. 자동(`auto = true`) 전달은 원할 때만 켜세요.
- `ROLES_DEBUG=1`이면 훅이 처리한 이벤트를 `<state dir>/dispatch.log`에 남깁니다.

## 개발
```sh
python3 -m unittest discover -s tests     # 가짜 herdr(tests/fake_herdr.py) 기반 단위 테스트
sh tests/e2e.sh                           # 실제 herdr, 격리 세션 roles-e2e (claude 에이전트 2개, 토큰 소량)
```
e2e는 실제 LLM 에이전트를 쓰므로 가끔 타이밍으로 흔들립니다. 관찰된 것: 로스터에 답하는 중인 lead가 120초 안에 idle이
되지 않은 경우(실패 시 화면을 출력하도록 해 두었습니다), 긴 인계 텍스트 때문에 `read --lines`가 좁으면 응답이 창 밖으로 밀리는
경우(400줄로 늘림). `ROLES_DEBUG=1`로 남는 `dispatch.log`가 원인 파악에 쓰입니다.

`tests/e2e.sh`는 모든 herdr 호출에 `HERDR_SOCKET_PATH`를 명시합니다. herdr pane 안에서는 이 변수가 `HERDR_SESSION`보다
우선하므로, 명시하지 않으면 실사용 세션이 대상이 됩니다. 자세한 실측 근거는 `docs/spike-results.md`를 보세요.

## 라이선스
MIT — `LICENSE` 참고.
