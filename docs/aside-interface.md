# Aside 접점

이 프로그램이 기대는 Aside의 명령·파일·도구 이름이다. Aside가 갱신되면 이 가운데 하나가 바뀌어 깨질 수 있다.
갱신 뒤에는 `check-aside`로 점검한다.

2026-11-15에 Aside CLI 1.26.1113.1717의 `--help`(`aside`, `exec`, `repl`, `session`)와 `guide` 첫 부분을 읽었다.
실제 Aside로 이 프로그램을 돌린 점검은 아직 없다(사용자 승인 대기).

## CLI 명령

| 접점 | 쓰는 곳 | CLI 도움말에 있음 |
| --- | --- | --- |
| `aside --version` | 사전 확인, 실행 기록 | 예 |
| `aside account status --account <계정>` 출력의 `<계정> … signed in` 줄 | 로그인 확인 | 명령은 예, 출력 모양은 아니오 |
| 앱이 꺼졌을 때 문구(`isn't running`, `Start Aside Browser`, `is not connected to the daemon`) | 앱 꺼짐 판정 | 아니오 |
| `aside host profiles --host <호스트>` 출력의 `<UUID> <이름> connected` 줄 | 프로필 하나로 정하기 | 명령은 예, 출력 모양은 아니오 |
| `aside exec --account --host [--profile] --model --effort --speed <지시>` | 조사·대조 세션 | 예 |
| 표준오류의 `created new session: <ID>` 줄 | 세션 ID 읽기 | 아니오(도움말은 세션 ID가 표준오류로 간다고만 적음) |
| 세션을 띄우지 못한 `exec`의 표준출력 또는 표준오류 ` • Error <문구>` 줄 | 기동 실패·쓸 수 없는 모델 판정 | 아니오 |
| `aside session stop --account <계정> <ID>` | 시간 초과·중단 때 이 프로그램의 세션만 멈춤 | 예 |
| `aside session list --account <계정>` 출력의 `<ID> <상태>` 줄 | 중단 때 실행 중인지 보기 | 명령은 예, 출력 모양은 아니오 |
| `aside repl --account --host [--profile] <스크립트>` | 근거 다시 열기, 브라우저 연결 확인 | 예(제한 120초) |

## 파일

| 접점 | 쓰는 곳 | 공개 문서 |
| --- | --- | --- |
| 세션 폴더 `<홈>/u/<번호>/sessions/<날짜>_<세션ID>/` | 결과 파일·세션 기록 찾기 | 아니오 |
| 세션 폴더의 `messages.jsonl`(role, model, provider, usage, stopReason, errorMessage, content) | 모델 확인, 사용량, 오류 분류, 대조 답, 잔액 부족 판정 | 아니오 |
| 세션 폴더 아래 `./artifacts/…`에 조사 세션이 저장한 결과 JSON | 조사 결과 받기 | 아니오 |
| 계정 폴더 아래 같은 경로(세션 폴더에 없을 때) | 조사 결과 받기 | 아니오 |

## REPL 전역 이름

`openTab`, `closeTab`, `listBrowserTabs`, `sleep`, `fetch`, `Buffer`, 탭의 `evaluate`·`content`·`url`·`title`·`frames`·`mouse.click`,
프레임의 `url`·`locator`. 도움말은 Playwright 꼴 JavaScript라고만 적는다.

## 조사 세션 도구 이름(지시문에 적는 것)

`fs.mkdir`, `fs.writeFile`, `aside.pdf.extractText`, `aside.pdf.renderPages`. 근거 도구 값 `aside_native_browser`.

`fs.readFile(<절대 경로>, "utf8")`: 조사 세션이 실행 기록 폴더의 지시 파일(`exec-<역할>/instructions.txt`)을 읽는 데 쓴다(2026-11-25 더함).
세션의 작업 폴더는 세션 폴더라서 지시 파일은 절대 경로(슬래시 표기)로 알려 준다.
2026-11-25에 실제 Aside로 한 건을 두 번 돌렸다(`claude-code/claude-sonnet-5-5`, medium). 1차·2차 세션 모두 첫 도구 호출로 지시 파일(약 5,500~5,900자)을
잘림 없이 읽고 결과 파일을 남겼다. 세션에 보낸 지시는 약 890자였다(이전에는 약 6천 자). 끝까지 돈 실행은 verified, 13항목 중 10항목 채택으로
11-15 측정의 같은 입력과 같았다. 못 읽으면 세션이 `INSTRUCTIONS_UNREADABLE <run_id>`로 끝나고 실패 사유가 `instructions_unreadable`로 남는다.
그때는 `[aside] instructions_file = false`로 끈다.

## 점검

```
python -m pubfinder check-aside --model provider/model-name --effort low
python -m pubfinder check-aside --with-exec --model provider/model-name --effort low
```

기본 점검은 모델을 부르지 않는다(판, 로그인, 프로필, REPL, `https://example.com/` 화면, 공개 PDF 하나).
`--with-exec`는 웹을 쓰지 않는 짧은 세션 하나를 띄워 세션 ID 읽기, 결과 파일, 세션 기록, 세션 목록까지 본다. 모델 사용량을 쓴다.

2026-11-25 사용자 PC의 화면 점검(모델까지 점검)에서 세션 ID 읽기와 세션 폴더 찾기가 맞았다.
공급자가 잔액 부족으로 거절한 세션의 `messages.jsonl`은 `model`·`provider`가 붙은 assistant 줄 하나에
`errorMessage`가 `400 {"type":"error","error":{"type":"invalid_request_error","message":"Your credit balance is too low ..."}}` 꼴이었다
(토큰 0, 최종 답 없음). 이 모양으로 잔액 부족을 가르고 대체 모델로 바꾼다. 결과 파일 접점은 이 점검에서 보지 못했다.

같은 날 사용자 승인을 받고 이 저장소에서 `claude/claude-sonnet-5-5`로 모델 세션 하나를 띄웠다. Aside는 세션을 만든 뒤(`created new session` 줄)
표준오류에 ` • Error Requested model claude/claude-sonnet-5-5 is not available for this account. Connect the provider or choose an available model.`을
쓰고 종료 코드 1로 끝났다. 세션 폴더는 생겼지만 `messages.jsonl`은 없었다. 그전까지는 표준출력만 읽어 이 문구를 놓쳤다.
Claude 구독으로 연결한 모델의 Aside 이름은 `claude-code/claude-sonnet-5-5`다(같은 PC에서 Aside를 쓰는 다른 프로젝트 설정을 사용자가 확인해 알려 줌).
이 이름으로 같은 점검 세션을 다시 띄우니 종료 코드 0, 세션 폴더의 결과 파일 읽기, 세션 기록 모델 `claude-sonnet-5-5`(모델 확인 matched), 세션 목록 `idle`까지 모두 맞았다.
