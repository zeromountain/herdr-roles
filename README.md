# herdr-roles

herdr pane에 **역할**을 부여하고, 메인 세션에서 **팀을 구성**(나머지 역할 pane을 자동 스폰)하며,
역할 사이에서 **작업을 인계**하는 [herdr](https://herdr.dev) 플러그인입니다.

```
메인 pane(Planner, lead) ──team-up──▶  Implementer · Reviewer · Tester · Runner pane 4개 스폰
                          ◀─ send / read ─▶  역할 이름으로 지시하고 결과를 읽음
                          plan → build → review ⇄ fix   (done 감지 → 다음 역할로 인계)
```

개발팀 5역할을 지정하면 **메인 pane이 lead 1개를 맡고 나머지 4개만 새로 스폰**합니다.

## 설치

```sh
herdr plugin install zeromountain/herdr-roles   # 또는 로컬 클론을 링크: herdr plugin link ~/dev/herdr-roles
~/dev/herdr-roles/bin/roles init           # 예제 설정을 ~/.config/herdr/plugins/config/herdr-roles/ 로 복사
```

Python 3.11+ (표준 라이브러리만 사용), herdr 0.9.1+.

> 플러그인 레지스트리는 herdr 전체(모든 세션)에 적용됩니다. 훅은 상태 파일이 없는 workspace에서는 즉시 종료하므로
> 기존 세션에는 영향이 없습니다.

## 빠른 시작

메인 pane(에이전트를 띄운 pane)의 **셸/에이전트에서**:

```sh
bin/roles team-up --dry-run          # 무엇을 스폰할지 미리 보기
bin/roles team-up --team dev         # 이 pane = lead, 나머지 역할 pane 스폰 + 에이전트 기동 + 로스터 주입
bin/roles status                     # 팀/워크플로우 상태
bin/roles send --role reviewer --text "auth.py 리뷰해줘"
bin/roles read --role reviewer --lines 200
bin/roles run --workflow feature --input "로그인 기능 추가"
bin/roles advance                    # 반자동 단계에서 다음 역할로 인계
bin/roles team-down                  # 스폰한 pane만 정리 (lead·직접 만든 pane은 유지)
```

`team-up`은 **멱등**입니다. 부족한 pane만 만들고, 죽은 pane은 다시 만들며, 에이전트가 사라진 멤버는 다시 시작합니다.
이미 정상인 pane은 건드리지 않습니다.

lead가 에이전트(Claude 등)면 로스터(팀원 목록과 `send`/`read` 사용법)를 프롬프트로 받습니다.
그래서 lead에게 "개발팀 구성해줘"라고 시키면 lead가 직접 `bin/roles team-up`을 실행할 수 있습니다.
lead가 `working`이면 로스터 주입을 보류했다가 idle이 될 때 전달합니다.

메뉴에서도 쓸 수 있습니다: 액션 `herdr-roles.assign`(메뉴 팝업), `herdr-roles.team-up`, `herdr-roles.team-down`,
`herdr-roles.status`, `herdr-roles.advance`, `herdr-roles.reapply`. 플러그인 pane: `picker`(메뉴), `board`(상태 대시보드).

> 액션은 **호출한 pane이 아니라 포커스된 pane**을 대상으로 합니다(herdr 액션 컨텍스트의 동작).
> 에이전트 셸에서 쓸 때는 `bin/roles`가 `$HERDR_PANE_ID`(진짜 호출 pane)를 쓰므로 이쪽이 정확합니다.

## 설정 (`~/.config/herdr/plugins/config/herdr-roles/`)

| 파일 | 내용 |
|---|---|
| `roles.toml` | 역할: `label`, `agent`(= `herdr agent start --kind` 값) 또는 `command`(셸 역할), `prompt`, `agent_args` |
| `teams/*.toml` | 팀: `[[member]]` `role`, `lead`(정확히 1개), `split`/`of`(배치 힌트), `ratio`(선택), `count` |
| `workflows/*.toml` | 흐름: `[[step]]` `id`, `role`, `from`(문자열 또는 목록), `handoff`, `auto`, `template`, `when`, `max_iterations` |
| `config.toml` | `default_team`, `default_workflow`, `max_spawn`(기본 8), `handoff_lines`, `spawn_timeout_ms`, `idle_grace_s` |

`examples/`에 개발팀(5역할)과 `plan → build → review ⇄ fix` 워크플로우가 있습니다. 설정은 실행 전에 전부 검증됩니다
(lead 1개, 정의된 역할, 앞선 멤버만 `of` 참조, 존재하는 `from` 등). 오류가 있으면 pane을 하나도 만들기 전에 실패합니다.

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
- 상태 파일은 **세션 이름으로 구분**됩니다(훅은 기본 세션과 이름 있는 세션 모두에서 실행되기 때문).

## 알아 둘 점
- **trust 프롬프트**: Claude는 폴더마다 "신뢰하시겠습니까?"를 묻고, 그동안 `agent start`가 `blocked`로 실패합니다.
  해당 역할은 `failed`로 기록되고 나머지 팀은 계속 만들어집니다. `agent start`는 pane이 셸 프롬프트일 때만 되므로,
  trust 프롬프트를 정리해 pane을 셸로 되돌린 뒤 `team-up`을 다시 실행하면 같은 pane에서 재시도합니다(자동으로 신뢰 처리하지 않습니다).
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
