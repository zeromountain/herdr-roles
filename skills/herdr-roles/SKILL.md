---
name: herdr-roles
description: herdr pane 에 역할을 나눈 에이전트 팀을 띄우고 일을 나눠 맡기는 `roles` CLI (herdr-roles 플러그인). 사용자가 "roles team-up", "team-up", "팀 구성해줘", "팀 띄워줘", "개발팀 꾸려줘", "팀원에게 시켜줘", "팀 여러 개 병렬로", "팀마다 나눠서 동시에", "fleet", "roles send/read/status/team-down", "herdr-roles" 처럼 말하면 사용한다. 셸 명령이므로 직접 실행한다.
---

# herdr-roles (`roles` CLI)

`roles` 는 herdr 플러그인의 **셸 명령**입니다. 사용자가 `roles team-up` 이나 "팀 구성해줘"라고 하면
되묻지 말고 셸에서 바로 실행하세요. 슬래시 명령이나 다른 도구가 아닙니다.

## 팀 띄우기

```sh
roles team-up --dry-run   # 무엇을 띄울지 미리 보기 (아무것도 만들지 않음). 처음이면 먼저 보여 주세요
roles team-up             # 이 pane = 리드, 나머지 역할 pane 을 스폰하고 에이전트를 띄움
roles team-up --team <이름>   # 기본 팀이 아닌 다른 팀
```

- 실행한 pane(지금 당신이 있는 pane)이 리드가 됩니다. 팀이 뜨면 팀원 목록과 사용법이 프롬프트로 다시 들어옵니다.
- 이미 팀이 있으면 부족한 pane 만 다시 만듭니다(멱등). 다시 실행해도 안전합니다.
- 설정은 전역 < 프로젝트(`.herdr-roles/`, 커밋해 공유) < 사용자(`.herdr-roles/local/`, 나만 씀) 순으로 겹쳐집니다.
  `roles config` 로 지금 적용되는 계층과 각 계층의 역할·팀을 볼 수 있습니다.
  프로젝트 전용 팀을 만들고 싶다는 요청이면 사용자에게 저장소 폴더의 터미널에서 `roles setup --scope project`
  (나만 쓸 설정이면 `--scope user`) 를 실행하라고 안내하세요
  (질문에 답하는 마법사라 에이전트가 직접 실행하지 않습니다).

## 팀원에게 일 맡기기

```sh
roles send --role <역할> --text "<지시>"   # 역할 이름으로 지시
roles read --role <역할> --lines 200      # 그 역할의 최근 출력 읽기
roles status                             # 팀·워크플로우 상태
roles run --workflow <이름> --input "<요청>"  # 정해 둔 순서대로 역할을 거치게 함
roles team-down                          # 다 쓰면 정리 (스폰한 pane 만 닫음, 사용자가 요청할 때만)
```

`send` 로 맡긴 일은 끝나면 결과가 **보낸 pane 으로 자동 보고**됩니다(`[herdr-roles] ... 작업 완료 보고`).
계속 `read` 로 확인하며 기다리지 말고 다른 일을 하거나 턴을 마치세요. 보고를 받으면 다음 단계를 진행합니다.

## 여러 팀을 병렬로 지휘할 때 (오케스트레이터)

사용자가 일을 여러 팀에 나눠 **동시에** 시키라고 하면, 지금 pane 을 오케스트레이터로 두고 팀마다 새 workspace 를 띄웁니다.
일을 서로 겹치지 않는 단위로 나누고, 팀마다 짧은 영문 이름과 구체적인 지시를 정하세요.

```sh
roles fleet-up auth billing --task "<auth 팀 지시>" --task "<billing 팀 지시>"   # --task 는 이름 순서대로 하나씩
roles fleet-up docs=writers --task "..."     # 이름=팀설정 으로 다른 팀 구성 사용 (기본: default_team)
roles fleet-up auth billing --worktree ...   # 같은 파일을 고칠 수 있는 일이면 팀마다 git worktree 에서
roles fleet-up api --cwd ~/dev/app --task "..." # 다른 저장소에서 일할 팀 (기본: 이 셸의 현재 폴더)
roles fleet                                  # 팀별 상태 (working = 보고 대기 중)
roles send --fleet auth --text "<추가 지시나 답>"   # 그 팀 리드에게 새 요청
roles read --fleet auth                      # 그 팀 리드 화면 (보고가 늦을 때만)
roles fleet-down [이름]                      # 다 끝나고 사용자가 원할 때만 닫기
```

- 각 팀의 리드가 팀원에게 일을 나누고, 팀의 일이 끝나면 `[herdr-roles] 팀 '<이름>' ... 작업 완료 보고` 가 이 pane 으로 옵니다.
  보고를 기다리며 `fleet`/`read` 를 반복하지 말고 턴을 마치세요. 보고가 모이면 결과를 합쳐 사용자에게 알립니다.
- 리드가 결정을 묻는 보고를 보내면 `roles send --fleet <이름>` 으로 답하세요.
- 팀은 한 팀씩 차례로 뜨지만(팀원마다 몇 초) 뜬 팀부터 바로 일을 시작해 팀끼리 동시에 작업합니다.

## fleet 팀의 리드일 때 (`이 팀은 오케스트레이터가 병렬로 띄운 팀` 을 받은 경우)

- `[herdr-roles] 오케스트레이터의 요청` 을 받으면 팀원에게 나눠 맡기고(`roles send --role ...`) 턴을 마치세요.
  이때는 보고하지 마세요.
- 팀원 보고가 모두 모여 요청한 일이 끝났을 때만 셸에서 결과를 보고하세요. 오케스트레이터에게 전달되고 전문은 파일로 남습니다.
  결정이 필요할 때도 같은 명령으로 질문을 보고하면 됩니다.
  ```sh
  roles report <<'EOF'
  한 일, 바뀐 파일, 남은 문제
  EOF
  ```

## 팀원일 때 (`[herdr-roles] 당신은 팀 ... 역할입니다` 를 받은 경우)

- 지시는 `[herdr-roles] <보낸 사람>의 요청` 머리말로 옵니다. 끝나면 셸에서 결과를 보고하세요:
  ```sh
  roles report <<'EOF'
  무엇을 했고, 결과와 남은 문제
  EOF
  ```
  보낸 사람에게 앞부분과 전문 파일 경로가 전달됩니다(요청한 사람이 없으면 리드에게). 길어도 잘리지 않습니다.
- 다른 역할의 확인이나 정보가 필요하면 그 역할에게 직접 `roles send --role <역할> --text "..."` 하세요.
  답은 끝나는 대로 당신에게 오고, 리드에게는 한 줄 참조가 갑니다. 막히거나 결정이 필요하면 리드 역할에게 보내세요.

## 실패할 때

| 출력 | 할 일 |
|---|---|
| `roles: command not found` | 이 스킬 폴더의 실제 위치(심볼릭 링크를 따라간 곳) 기준 `../../bin/roles` 를 직접 실행하세요. 예: `python3 ~/.config/herdr/plugins/github/herdr-roles-*/bin/roles team-up`. 사용자에게 `roles install` 로 명령을 등록하라고 알려 주세요. |
| `대상 pane을 알 수 없습니다` | herdr pane 밖에서 실행된 것입니다(`$HERDR_PANE_ID` 없음). herdr 안의 pane 에서 실행해야 한다고 사용자에게 알리세요. |
| `설정된 역할이 없어 설정 창을 열었어요` | 사용자가 herdr 팝업에서 팀을 정해야 합니다. 답한 뒤 `roles team-up` 을 다시 실행하세요. |
| `Python 3.11+ 가 필요한데` | 오류에 나온 해결법을 사용자에게 그대로 전하세요. |
| 팀원이 `failed` | Claude·Codex 폴더 신뢰(trust) 질문일 수 있습니다. 그 pane 에서 사용자가 답한 뒤 `roles team-up`(fleet 이면 `roles fleet-up <이름>`) 을 다시 실행하세요. 매번 답하기 번거롭다면 사용자에게 설정 `trust_folders = true` 를 안내하세요 (사용자가 원할 때만 켭니다). |

## 하지 말 것

- `roles setup` 을 그냥 실행하지 마세요. 질문에 답해야 하는 마법사라 에이전트 셸에서는 첫 질문에서 끝납니다.
  사용자가 프리셋을 고르면 `yes '' | roles setup --preset <프리셋>` 으로 추천값을 쓸 수 있습니다(목록: `roles setup --list-presets`).
  이미 역할이 있으면 `--force` 가 필요하고 기존 설정을 덮어쓰니, 사용자가 원할 때만 쓰세요.
- 팀원 pane 에 직접 타이핑하지 말고 `roles send` 를 쓰세요.
- 사용자가 병렬 팀을 원하지 않으면 `fleet-up` 을 쓰지 마세요. 팀마다 에이전트가 여러 개 떠서 비용이 큽니다.
