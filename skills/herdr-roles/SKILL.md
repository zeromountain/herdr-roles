---
name: herdr-roles
description: herdr pane 에 역할을 나눈 에이전트 팀을 띄우고 일을 나눠 맡기는 `roles` CLI (herdr-roles 플러그인). 사용자가 "roles team-up", "team-up", "팀 구성해줘", "팀 띄워줘", "개발팀 꾸려줘", "팀원에게 시켜줘", "roles send/read/status/team-down", "herdr-roles" 처럼 말하면 사용한다. 셸 명령이므로 직접 실행한다.
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

## 팀원에게 일 맡기기

```sh
roles send --role <역할> --text "<지시>"   # 역할 이름으로 지시
roles read --role <역할> --lines 200      # 그 역할의 최근 출력 읽기
roles status                             # 팀·워크플로우 상태
roles run --workflow <이름> --input "<요청>"  # 정해 둔 순서대로 역할을 거치게 함
roles team-down                          # 다 쓰면 정리 (스폰한 pane 만 닫음, 사용자가 요청할 때만)
```

`send` 뒤에는 바로 결과가 나오지 않습니다. 시간을 두고 `read` 나 `status` 로 확인하세요.

## 실패할 때

| 출력 | 할 일 |
|---|---|
| `roles: command not found` | 이 스킬 폴더의 실제 위치(심볼릭 링크를 따라간 곳) 기준 `../../bin/roles` 를 직접 실행하세요. 예: `python3 ~/.config/herdr/plugins/github/herdr-roles-*/bin/roles team-up`. 사용자에게 `roles install` 로 명령을 등록하라고 알려 주세요. |
| `대상 pane을 알 수 없습니다` | herdr pane 밖에서 실행된 것입니다(`$HERDR_PANE_ID` 없음). herdr 안의 pane 에서 실행해야 한다고 사용자에게 알리세요. |
| `설정된 역할이 없어 설정 창을 열었어요` | 사용자가 herdr 팝업에서 팀을 정해야 합니다. 답한 뒤 `roles team-up` 을 다시 실행하세요. |
| `Python 3.11+ 가 필요한데` | 오류에 나온 해결법을 사용자에게 그대로 전하세요. |
| 팀원이 `failed` | Claude 폴더 신뢰(trust) 질문일 수 있습니다. 그 pane 에서 사용자가 답한 뒤 `roles team-up` 을 다시 실행하세요. |

## 하지 말 것

- `roles setup` 을 그냥 실행하지 마세요. 질문에 답해야 하는 마법사라 에이전트 셸에서는 첫 질문에서 끝납니다.
  사용자가 프리셋을 고르면 `yes '' | roles setup --preset <프리셋>` 으로 추천값을 쓸 수 있습니다(목록: `roles setup --list-presets`).
  이미 역할이 있으면 `--force` 가 필요하고 기존 설정을 덮어쓰니, 사용자가 원할 때만 쓰세요.
- 팀원 pane 에 직접 타이핑하지 말고 `roles send` 를 쓰세요.
