# Spike 0 결과 (herdr 0.9.1, 2026-10-06 실측)

격리 세션(`roles-dev`)에서 throwaway 플러그인(`spike/plugin/`)으로 측정. 세션·링크는 정리 완료.

## 판정 요약

| # | 질문 | 결과 |
|---|---|---|
| ① | `[[events]]` 훅이 `pane.agent_status_changed` 허용? | **허용·실행 확인**. `pane.created`, `pane.closed`도 실행됨 → **상주 watcher 불필요, 무데몬 디스패처 확정** |
| ② | 컨텍스트 스키마 / `contexts` 허용값 | `contexts` = `global`·`workspace`·`tab`·`pane`·`selection`. 스키마는 아래 |
| ③ | CLI JSON 출력 | **기본이 JSON** (`{"id","result":{...}}`). 단 `agent read`는 일반 텍스트 |
| ④ | 서버 재시작 후 | pane_id·cwd **유지**. `title`/`tokens`/`state_labels` **전부 소실**. `[[startup]]` 훅은 복원 직후 실행 |
| ⑥ | 내장 감지 pane에 플러그인 메타데이터 | `title`, `tokens`, `state_labels` **저장됨**(`--applies-to-source` 불필요). 화면 렌더는 TUI 미접속이라 미확인 |
| ⑦ | 이벤트 페이로드 | 아래 |
| ⑧ | 스폰 | `pane split`이 새 pane_id JSON 반환, `--cwd` 적용, 새 pane에서 즉시 `agent start` 성공(~4s, `interactive_ready:true`). **같은 pane 반복 분할+고정 ratio는 붕괴**(높이 1/3/8/28) |
| ⑨ | 메인에서의 호출 경로 | `plugin action invoke`는 **호출 pane이 아닌 포커스 pane**을 컨텍스트로 씀(`--pane` 옵션 없음). 모든 pane 셸에는 `HERDR_PANE_ID`·`HERDR_SOCKET_PATH`가 주입되고, `HERDR_SESSION`은 **이름 있는 세션에서만** 주입됨(기본 세션 pane에는 없음 → `${HERDR_SESSION:-default}`) |
| ⑩ | 실행 중 에이전트에 프롬프트 | `agent prompt <name> <text> --wait --until idle` 동작(~2.6s) |

## 설계에 미치는 변경 (계획 파일 `~/.claude/plans/herdr-pane-hidden-yeti.md`에 반영 완료)

1. **팀 스폰은 `bin/roles team-up`을 메인 에이전트 셸에서 직접 실행**하는 경로를 1순위로 한다. 호출 pane = `$HERDR_PANE_ID`, 세션 = `$HERDR_SOCKET_PATH`. 메뉴/키바인드용 `plugin action invoke`는 포커스 pane 기준이므로 대상 pane을 명시 인자로 받는다.
2. **플러그인 레지스트리와 state dir은 전역**이다(`~/.config/herdr/plugins.json`, `~/.local/state/herdr/plugins/<id>/`). 훅은 기본 세션과 이름 있는 세션 **모두**에서 실행된다. 상태 파일은 `STATE_DIR/<HERDR_SESSION|default>/<workspace_id>.json`로 **세션 키를 포함**하고, 훅은 항상 이벤트가 온 세션의 `HERDR_SOCKET_PATH`로 CLI를 호출한다.
3. 훅은 `HERDR_SOCKET_PATH`를 이벤트가 발생한 세션 것으로 받으므로 래퍼는 환경변수를 그대로 상속한다. **개발 중 명령은 `HERDR_SOCKET_PATH`를 명시해야 한다**(herdr pane 안에서 실행하면 `HERDR_SESSION`보다 `HERDR_SOCKET_PATH`가 우선해 기본 세션이 대상이 된다).
4. `agent_status_changed` 페이로드에 `revision`이 없다 → 중복 방지 키는 `(pane_id, agent_status, 수신 시각/단조 카운터)`.
5. 분할 비율은 직접 계산: N개를 한 열에 쌓을 때 마지막으로 만든 pane을 대상으로 `ratio = 1/(남은 pane 수)`로 분할하거나, `pane layout`으로 확인 후 `pane resize`로 보정한다.
6. 재시작 복구: `[[startup]]` 훅에서 상태 파일로 메타데이터 재적용 + 존재하지 않는 pane_id 정리. pane_id가 유지되므로 `slot_id` 매칭은 불필요(서버 재시작 한정; 워크스페이스 재생성 시에는 새 id).
7. 매니페스트는 **알 수 없는 이벤트명도 링크 시 거부하지 않는다**(`totally.fake_event` 수락). 이벤트명은 실행으로만 검증된다 → 테스트에 훅 실행 확인 포함.

## 실측 스키마

- 훅 cwd = 플러그인 루트. 훅 env: `HERDR_PLUGIN_{ID,ROOT,STATE_DIR,CONFIG_DIR,EVENT,EVENT_JSON,CONTEXT_JSON}`, `HERDR_{BIN_PATH,ENV,SOCKET_PATH,SESSION,PANE_ID,TAB_ID,WORKSPACE_ID}`. 액션은 `HERDR_PLUGIN_ACTION_ID` 추가.
- `HERDR_PLUGIN_CONTEXT_JSON`:
  `{"workspace_id","workspace_label","workspace_cwd","tab_id","tab_label","focused_pane_id","focused_pane_cwd","focused_pane_status","invocation_source":"api|cli","correlation_id"}`
- 이벤트(`HERDR_PLUGIN_EVENT_JSON`, 이벤트명은 JSON에서 `_` 표기):
  - `pane_created`: `{"event","data":{"type","pane":{pane_id,terminal_id,workspace_id,tab_id,focused,cwd,foreground_cwd,agent_status,scroll,revision}}}`
  - `pane_agent_status_changed`: `data = {type, pane_id, workspace_id, agent_status, agent}`
  - `pane_closed`: `data = {type, pane_id, workspace_id}`
- `agent start <name> --kind <k> --pane <id> [--timeout ms]` → `{agent:{name,pane_id,interactive_ready,agent_status,revision,state_change_seq}, argv}`.
- 기본 세션에서 `workspace close`를 했을 때 `pane.closed` 훅 실행 흔적이 없었다(워크스페이스 단위 닫기는 별도 이벤트일 가능성; 미확인 → 상태 정리는 `reapply`에서 `pane list`와 대조해 보정).

## 아직 미확인
- 메타데이터가 TUI 사이드바/pane 헤더에 **실제로 어떻게 보이는지**(헤드리스라 데이터만 확인).
- `workspace.*` 훅 이벤트명(`workspace.closed` 등)이 훅으로 실행되는지.
- 에이전트 kind별(codex/gemini) 기동 시간과 신뢰 폴더 프롬프트로 인한 `blocked` 여부(claude만 측정).
