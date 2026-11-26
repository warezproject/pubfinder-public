# pubfinder

논문명, 학술대회명, 저자명을 넣으면 그 논문의 학술대회 발표 정보를 웹에서 조사해 돌려주는 프로그램이다.
Windows에서 exe를 더블클릭해 창으로 쓰거나 명령줄로 쓴다.
발표일, 세션, 행사 기간, 장소, 개최국 같은 항목마다 값과 근거 주소, 원문 인용을 함께 낸다.

조사는 사용자가 설치한 [Aside](https://aside.com) 브라우저 에이전트로 한다. 같은 논문을 Aside 세션 두 개가 서로 모르게 따로 조사하고,
두 결과가 맞는 항목만 채택한다. 학술지 논문은 다루지 않는다.

조사 한 건에 보통 1~4분(길면 8분), 모델 사용료 0.2~0.8달러(평균 약 0.35달러)가 든다. 사용료는 Aside에 연결한 모델 계정에서 나간다.

## English summary

pubfinder looks up conference presentation details for a paper — presentation date, session, time, event dates,
organizer, venue, host country and domestic/international classification — from its title, conference name and authors.
Two [Aside](https://aside.com) browser-agent sessions research the same paper independently, the program re-opens the
cited sources to check the quotes, and only fields on which both sessions agree are adopted.
The Windows app, outputs and documentation are in Korean only. It needs Aside with a connected model provider,
and each lookup uses model credits (about USD 0.35 on average).

## Aside 준비

창으로 쓰든 명령줄로 쓰든 먼저 한 번 해 둔다. 명령은 시작 메뉴에서 PowerShell을 열고 한 줄씩 붙여 넣는다.

1. [aside.com](https://aside.com)에서 Aside를 설치하고 Aside 앱에서 로그인한다.
2. Aside 앱 설정에서 조사에 쓸 모델 공급자(예: Anthropic)를 연결한다.
3. Aside CLI가 있는지 확인한다. 이 프로그램은 기본으로 아래 경로의 CLI를 쓴다.

   ```powershell
   & "$env:LOCALAPPDATA\Aside\CLI\current\aside.exe" --version
   ```

   파일이 없으면 [Aside 문서](https://docs.aside.com)의 CLI 안내를 따른다. 다른 경로에 있으면 창의 설정 탭(명령줄은 `--aside-executable`)에 적는다.
4. CLI를 로그인시킨다. 화면에 나온 코드를 Aside 앱의 Settings > Account > Devices에서 승인한다.

   ```powershell
   & "$env:LOCALAPPDATA\Aside\CLI\current\aside.exe" login
   & "$env:LOCALAPPDATA\Aside\CLI\current\aside.exe" account status
   ```

   `account status` 결과에 `u0 … signed in`이 보이면 된다.

## 창으로 쓰기(Windows exe)

Python이나 명령어 없이 Windows 창에서 쓴다. 웹 브라우저나 내 PC 안 서버는 띄우지 않는다.

1. GitHub의 [Releases](https://github.com/warezproject/pubfinder-public/releases/latest) 페이지에서 `pubfinder-windows.zip`을 받아 압축을 푼다. 직접 만들려면 [exe 만들기](#exe-만들기)를 본다.
2. `pubfinder.exe`를 더블클릭한다. 처음 실행할 때 Windows의 PC 보호 화면이 뜨면 추가 정보를 누르고 실행을 누른다.
   서명하지 않은 프로그램이라 뜨는 경고다. 기관 PC에서는 보안 정책 때문에 실행이 막힐 수 있다.
3. pubfinder 창이 뜬다. 설정 탭에서 모델을 확인하고 추론 강도를 골라 저장한 뒤 점검을 누른다.
   모델은 처음에 `claude-code/claude-sonnet-5-5`(Aside에 Claude 계정으로 연결한 모델)이고, `주 모델을 못 쓸 때 쓸 모델`은
   `anthropic/claude-sonnet-5-5`(Anthropic API)다. 다른 모델을 쓰려면 이름을 Aside에 나온 그대로 적는다.
   칸 아래 `점검·조사에 쓰는 설정` 줄에 실제로 쓰는 값이 나온다.
   여섯 항목이 모두 통과하면 준비가 끝났다. 통과하지 못한 항목에는 까닭과 할 일이 함께 나온다.
   점검과 조사는 저장된 설정으로 돈다. 저장하지 않은 변경이 있으면 점검이나 조사 시작을 누를 때 먼저 저장한다.
   첫 줄 `조사 설정(모델·추론 강도)`이 실패면 모델이나 추론 강도가 비어 있다.
   주 모델이 사용량 한도, 잔액(크레딧) 부족, 공급자 연결·인증 문제로 거절되면 그 세션을 `주 모델을 못 쓸 때 쓸 모델`로 한 번 다시 한다.
   결과의 주의 칸과 점검의 `대체 모델로 전환` 줄에 바꾼 사실이 남는다. 모델 이름 오타 같은 다른 오류에는 바꾸지 않는다.
   이 칸을 비워 저장하면 바꾸지 않는다.
4. 한 건 조사 탭에 논문명, 학술대회명, 저자, 연도를 넣고 조사 시작을 누른다. 진행 단계가 창에 나오고, 끝나면 오른쪽에 항목별 결과가 나온다.
   표에서 항목을 고르면 두 조사가 낸 값, 근거 주소, 원문 인용이 아래에 나오고, 근거 열기를 누르면 그 주소를 웹 브라우저로 연다.
5. 끝낼 때는 창을 닫는다. 조사 중이면 멈출지 묻고, 진행 중인 Aside 세션을 멈춘 뒤 끝난다.

| 탭 | 하는 일 |
| --- | --- |
| 한 건 조사 | 논문 한 편을 조사하고 항목별 결과, 근거, 두 조사가 낸 값을 본다 |
| 여러 건 조사 | 엑셀에서 CSV로 저장한 표를 골라 차례로 조사한다. 예시 CSV 저장으로 양식을 받는다 |
| 지난 결과 | 지난 조사를 다시 보고 골라서(Ctrl·Shift로 여러 줄) 또는 모두 엑셀(CSV)로 저장한다. 결과 폴더를 연다 |
| 설정 | 모델, 주 모델을 못 쓸 때 쓸 모델, 추론 강도, 조사 제한 시간, Aside CLI 경로, 한글 장소 규칙. Aside 점검 |

- 설정과 결과는 exe가 있는 폴더의 `pubfinder.local.toml`과 `pubfinder-runs` 폴더에 남는다. 그 폴더에 쓸 수 없으면 `%LOCALAPPDATA%\pubfinder`를 쓴다.
  새 판을 다른 폴더에 풀면 설정을 다시 정한다. 예전 폴더의 `pubfinder.local.toml`을 새 exe 옆으로 옮겨 와도 된다.
- exe를 다시 더블클릭하면 새로 띄우지 않고 이미 떠 있는 창을 앞으로 가져온다. 같은 Aside를 함께 쓰므로 한 번에 하나만 돈다.
- CSV는 엑셀의 `CSV UTF-8`로 저장하기를 권한다. 일반 `CSV`(한국어 Windows는 CP949)로 저장한 파일도 읽는다.

### exe 만들기

Python이 있는 PC에서 저장소를 받은 뒤 실행한다. 저장소 안 `.venv-build`에 PyInstaller를 설치해 만들고 전역 Python은 건드리지 않는다.

```powershell
python scripts/build_exe.py
```

`dist/pubfinder.exe`와 `dist/pubfinder-windows.zip`이 생긴다. zip에는 exe, 처음 쓰는 법, 제3자 고지, licenses 폴더가 들어간다. 제3자 고지에는 exe에 들어간 구성 요소의 판과 라이선스를 적는다. 구성 요소는 Python과 그에 딸린 OpenSSL·libffi·bzip2·xz·expat·libmpdec, Tcl/Tk, pypdf, PyInstaller 부트로더, Microsoft C 런타임이다. licenses 폴더에는 그 원문을 넣는다. 원문은 `scripts/third_party/`에 있고, 빌드용 Python의 판(지금 3.11.9)이 원문의 판과 다르면 빌드를 멈춘다. 소스에서 창만 띄우려면 [명령줄로 쓰기](#명령줄로-쓰기) 2단계대로 설치한 뒤 `python -m pubfinder app`.

## 명령줄로 쓰기

Windows PowerShell 기준이다. 아래 명령은 PowerShell 창에 한 줄씩 붙여 넣는다.

### 1. Python 설치

[python.org](https://www.python.org/downloads/)에서 Python 3.11 이상을 받아 설치한다.
설치 첫 화면에서 Add python.exe to PATH를 체크한다. 설치가 끝나면 PowerShell을 새로 열고 확인한다.

```powershell
python --version
```

### 2. 프로그램 받기와 설치

git이 없으면 GitHub 저장소 화면의 Code > Download ZIP으로 받아 압축을 풀어도 된다.

```powershell
git clone https://github.com/warezproject/pubfinder-public.git
cd pubfinder-public
python -m pip install -e .
```

### 3. 설정 파일 만들기

프로그램 폴더에 `pubfinder.local.toml` 파일을 만들고 아래 내용을 넣는다. 모델과 추론 강도는 기본값이 없어서 꼭 정해야 한다.
아래 모델은 측정에 쓴 값이다. Aside에 연결한 다른 모델을 써도 된다.

```toml
[aside]
model = "anthropic/claude-sonnet-5-5"
effort = "medium"
```

`*.local.toml` 파일은 git에 올라가지 않는다.

### 4. 점검

```powershell
python -m pubfinder check-aside --config pubfinder.local.toml
```

결과 맨 위에 `"ok": true`가 나오면 준비가 끝났다. 이 점검은 모델을 부르지 않고 Aside 로그인, 브라우저 연결, 공개 화면과 PDF 다시 열기를 본다.
Aside 브라우저가 꺼져 있으면 켜고 다시 한다.

### 5. 첫 조사

```powershell
python -m pubfinder search --config pubfinder.local.toml --title "논문명" --venue "학술대회명 2026" --author "저자1" --author "저자2" --year 2026
```

저자가 여러 명이면 `--author`를 여러 번 쓴다. 연도는 선택이지만 넣기를 권한다.

## 결과 보기

창에서는 같은 내용이 표로 나온다. 명령줄에서는 조사가 끝나면 터미널에 요약이 나오고, 맨 아래에 결과 파일 경로가 나온다.
아래는 지어낸 자료로 만든 예시다.

```
상태: verified (같은 논문의 발표일·제목까지 확인됨)
입력: A Made-up Conference Paper / IMAGCONF 2026 / Alex Sample, Riley Fixture / 2026
판정 정책: cpf-reconcile-v1 · 같은 논문 합의: 예 · 채택 11/13
  ✓ 행사명: The 9th International Conference on Imaginary Results (IMAGCONF 2026) (인용 확인) https://imagconf.example.org/2026/
  ✓ 발표일: 2026-06-10 (인용 확인) https://imagconf.example.org/2026/program
  ✓ 행사 시작일: 2026-06-09 (인용 확인) https://imagconf.example.org/2026/
  ✓ 행사 종료일: 2026-06-11 (인용 확인) https://imagconf.example.org/2026/
  ✓ 주관: Imaginary Results Society (인용 확인) https://imagconf.example.org/2026/
  ✓ 장소: Busan, Republic of Korea (인용 확인) https://imagconf.example.org/2026/
  ✓ 개최국: Republic of Korea [KR] (인용 확인) https://imagconf.example.org/2026/
  ✓ 국내·국제 구분: international (인용 확인) https://imagconf.example.org/2026/
  · 한글 제목: 미채택 - 두 조사 모두 확인하지 못함
  ✓ 영문 제목: A Made-up Conference Paper (인용 확인) https://imagconf.example.org/2026/program
  ✓ 저자(원문 순서): Alex Sample, Riley Fixture (인용 확인) https://imagconf.example.org/2026/program
  ✓ 세션: B2: Probing Methods (인용 확인) https://imagconf.example.org/2026/program
  · 발표 시각: 미채택 - 한쪽 조사만 값을 냄
결과 파일: pubfinder-runs\20261115T101500-1a2b3c4d\result.json
```

- ✓는 채택, ·는 미채택이다. 미채택 줄에는 사유가 붙는다.
- 인용 확인은 근거 주소를 다시 열었을 때 인용 문장이 원문에 있었다는 뜻이다.
- 입력과 다른 값이 채택되면 항목 목록 아래(결과 파일 경로 바로 위) `주의:` 줄에 나온다. 같은 논문인지 직접 확인한다.

상태 값의 뜻

| 상태 | 뜻 |
| --- | --- |
| verified | 같은 논문 합의, 발표일과 제목 채택 |
| partial | 일부 항목만 채택 |
| not_verified | 채택 항목 없음 |
| not_found | 1차 조사가 논문을 찾지 못함(채택 항목 없음) |
| blocked | 1차 조사가 원문 접근이 막혀 끝났고 채택 항목 없음. 근거 다시 열기에서 막힌 것은 상태가 아니라 `captures`와 주의에 남는다 |
| failed | 조사 중단(Aside 준비 안 됨, 사용량 한도 등) |

결과 파일 `result.json`에는 두 조사가 각각 낸 값과 근거, 미채택 사유, 다시 열기 기록이 모두 들어 있다.
주요 칸은 아래 [결과 파일 칸](#결과-파일-칸)에 있다.

## 문제 해결

| 터미널 메시지 | 원인 | 할 일 |
| --- | --- | --- |
| `입력·설정 오류: Aside 모델을 정하세요` | 모델 미설정 | 설정 파일에 model·effort 넣기 |
| `입력·설정 오류: 연도 입력에서 4자리 연도를 읽지 못했습니다` | 연도 형식 | `2026`이나 `2026-06-10`으로 입력 |
| `aside_unavailable:aside_executable_missing` | Aside CLI 없음 | Aside 준비 3단계 확인, CLI 경로 지정 |
| `aside_unavailable:aside_not_running` | Aside 브라우저 꺼짐 | Aside 앱 켜기 |
| `aside_unavailable:aside_account_not_signed_in` | CLI 로그인 안 됨 | `aside login` |
| `aside_unavailable:aside_browser_unavailable` | 브라우저 연결 안 됨 | Aside 앱 다시 켜기, 프로필이 여럿이면 `--profile` |
| `primary:research_unavailable:out_of_credit` | 모델 공급자 잔액(크레딧) 부족 | 공급자 결제 화면에서 충전하거나 설정 `fallback_model`(창의 주 모델을 못 쓸 때 쓸 모델)에 다른 모델 적기 |
| `primary:research_unavailable` | 사용량 한도, 모델 미연결, 다른 모델 응답 | 결과 파일 `run.sessions.primary.errors` 확인, Aside 설정의 모델 연결 확인 |
| `primary:research_not_started` | Aside 세션 시작 실패 | 잠시 뒤 다시 실행 |
| `primary:incomplete` | 조사 시간 초과 | 다시 실행, 필요하면 `--research-timeout 1800` |
| `primary:result_missing` | 세션이 결과 파일 없이 끝남 | 다시 실행 |
| `primary:instructions_unreadable` | 조사 세션이 지시 파일(`instructions.txt`)을 읽지 못함 | 설정 파일 `[aside]`에 `instructions_file = false`를 넣어 지시를 통째로 보내기 |
| `…:invalid_result:…` | 조사 결과 형식 오류 | 다시 실행 |
| 상태 `not_found` | 논문을 찾지 못함 | 학술대회명·연도 확인. 다른 학회 논문일 수 있음 |
| 개최국·구분 미채택, 사유 `행사연도가 입력 연도와 같다는 확인이 없음` | 연도 없음 | `--year` 넣고 다시 실행 |

`primary`는 1차 조사, `review`는 2차 조사다. 조사 중 Ctrl+C로 멈추면 이 프로그램이 띄운 Aside 세션도 함께 멈춘다.

## 여러 건 조사

창에서는 여러 건 조사 탭에서 CSV를 골라 차례로 조사한다. 명령줄의 `search`는 한 번에 한 건을 조사하므로, 여러 건은 PowerShell로 차례로 돌린다.
아래 칸으로 된 CSV 파일을 UTF-8로 저장한다(엑셀에서는 CSV UTF-8 형식). 저자가 여러 명이면 세미콜론으로 나눈다.

```
title,venue,authors,year
첫 번째 논문 제목,학술대회명 2026,저자1; 저자2,2026
두 번째 논문 제목,다른 학술대회 2026,저자3,2026
```

```powershell
Import-Csv papers.csv | ForEach-Object {
  $authorArgs = @($_.authors -split ';' | ForEach-Object { '--author'; $_.Trim() })
  python -m pubfinder search --config pubfinder.local.toml --title $_.title --venue $_.venue @authorArgs --year $_.year
}
```

건마다 결과 파일이 `pubfinder-runs` 아래에 따로 생긴다. 다른 Aside 작업과 동시에 돌리지 않는다.

## 돌려주는 항목

| 묶음 | 항목 |
| --- | --- |
| 행사 7개 | 행사명, 행사 시작일, 행사 종료일, 주관, 장소, 개최국(ISO 2자리 코드), 국내·국제 구분 |
| 논문 6개 | 발표일, 한글 제목, 영문 제목, 저자(원문 순서), 세션, 발표 시각 |

## 조사 순서

1. 입력으로 1차 조사와 2차 조사 지시문을 만든다. 지시 본문은 실행 기록 폴더의 `exec-<역할>/instructions.txt`에 쓰고,
   세션에는 입력과 "이 파일을 읽고 따르라"는 짧은 지시만 보낸다(실제 보낸 글은 `prompt.txt`).
   설정 파일 `[aside] instructions_file = false`로 끄면 지시문 전체를 그대로 보낸다. 다른 PC의 Aside(host가 local이 아님)에는 늘 전체를 보낸다.
2. Aside 세션 두 개를 동시에 돌린다. 두 세션은 서로의 답을 보지 못한다.
3. 2차 조사가 원문 근거를 하나도 남기지 않았으면 한 번 다시 돌린다.
4. 두 조사가 적은 근거 주소를 프로그램이 Aside 브라우저로 다시 열어 인용이 원문에 있는지 본다.
5. 웹을 쓰지 않는 Aside 세션 하나가 두 결과를 항목별로 대조한다.
6. 채택 규칙으로 판정해 결과 JSON을 만든다.

## 채택 규칙

| 항목 | 채택 조건 |
| --- | --- |
| 모든 항목 | 1차 조사 결론이 not_found(논문 못 찾음)가 아님, 두 조사 모두 값을 냄, 대조 결정이 agree. 채택값은 1차 값 |
| 논문 6개 | 위 조건과 두 조사 모두 같은 논문이라고 판단, 대조도 같은 논문에 동의 |
| 개최국, 국내·국제 구분 | 위 조건과 두 조사의 행사연도가 입력 연도와 같음, 역할 표지가 있는 직접 인용이 두 조사 모두 다시 연 원문에서 확인됨 |
| 행사명·기간·주관·장소 | 공통 조건만. 같은 논문 합의가 없어도 채택 가능 |

개최국과 국내·국제 구분을 뺀 항목에서 인용 확인은 채택 조건으로 쓰지 않고 `quote_verified`로 기록만 한다.
대조 세션이 답을 못 내면 더 엄격한 문자열 일치 규칙으로 판정한다. 자세한 규칙은 [docs/adoption-rules.md](docs/adoption-rules.md)에 있다.

채택은 두 조사가 서로 모르게 같은 값을 냈다는 뜻이다. 공식 일정표와 맞춰 본 값은 아니다.

## 측정 결과

공개 일정표가 있는 학술대회 논문으로 실제 Aside를 돌린 결과다(모델 anthropic/claude-sonnet-5-5, 추론 강도 medium).

| 항목 | 2025년 행사 20건 | 2026년 행사 20건 |
| --- | --- | --- |
| 공식 URL을 찾아낸 건수 | 19/20 | 16/20 |
| 건당 시간 | 평균 124초 | 평균 138초 |
| 건당 모델 사용료 | 평균 0.34달러 | 평균 0.36달러 |

공식 URL을 찾아낸 건수는 두 조사가 모두 학회 공식 원문(프로그램·논문 목록·논문 페이지)에서 입력한 논문을 찾아 같은 논문이라고 맞춘 건수다.
못 찾은 것은 다음과 같다.
- 2025년 1건: 입력한 한글 제목이 공식 제목과 달라 한쪽 조사가 같은 논문으로 확정하지 않았다.
- 2026년 4건:
  - 입력한 학술대회가 실제 게재처와 달랐다(표본 오류). 다른 학회 논문과 워크숍 논문이 하나씩이다.
  - 같은 약칭의 다른 행사를 찾아갔다.
  - 공식 일정표에 논문별 목록이 없었다.

어느 경우에도 틀린 값을 채택하지는 않았다.
세부 내용은 [docs/measurement-2026-11-15.md](docs/measurement-2026-11-15.md)와
[docs/measurement-2026-events.md](docs/measurement-2026-events.md)에 있다.

## 명령과 옵션

### search

| 옵션 | 뜻 |
| --- | --- |
| `--title` | 논문명(한글 또는 영문) |
| `--venue` | 학술대회명 |
| `--author` | 저자명. 여러 명이면 여러 번 쓴다 |
| `--year` | 연도 또는 날짜. `2026`, `2026-06-10`, `06/10/2026`, `June 2026`, `2026년 6월` |
| `--model`, `--effort` | Aside 모델(provider/model)과 추론 강도. 설정 파일 값보다 먼저 |
| `--fallback-model` | 사용량 한도·잔액 부족·공급자 연결 문제로 `--model`이 거절되면 그 세션을 다시 돌릴 모델(선택, 명령줄에는 기본값 없음) |
| `--config` | 설정 파일(TOML) |
| `--out` | 실행 기록 폴더(기본 `pubfinder-runs`) |
| `--json` | 결과 JSON 전체를 표준출력으로 |
| `--no-korean-place-rule` | 국가명 없는 한글 장소를 한국 개최로 보는 규칙 끄기 |
| `--excluded-domain` | 근거로 받지 않을 도메인(기본 arxiv.org) |
| `--run-deadline` | 한 건 전체 상한(초, 기본 3600) |
| `--keep-captures` | 다시 연 원문 본문을 실행 기록에 남기기 |
| `--aside-executable`, `--account`, `--host`, `--profile` | Aside 실행 파일·계정·호스트·브라우저 프로필 |
| `--research-timeout` | 조사 세션 하나의 제한 시간(초, 기본 1200) |

연도를 넣지 않으면 학술대회명 속 4자리 연도가 하나일 때만 그 연도를 쓴다. 연도가 아예 없으면 개최국과 국내·국제 구분은 채택되지 않는다.
종료 코드는 0(조사 완료, 채택 여부와 무관), 1(조사를 끝내지 못함), 2(입력·설정 오류)다.

### verify

```powershell
python -m pubfinder verify pubfinder-runs\<실행 ID>\result.json
```

저장된 두 조사 보고, 인용 확인 요약, 대조 결정으로 판정을 다시 계산해 결과 파일과 같은지 본다.

### check-aside

```powershell
python -m pubfinder check-aside --config pubfinder.local.toml
python -m pubfinder check-aside --config pubfinder.local.toml --with-exec
```

Aside가 업데이트된 뒤 이 프로그램이 쓰는 명령·파일·브라우저 접점이 그대로인지 본다. 기본 점검은 모델을 부르지 않는다.
`--with-exec`는 웹을 쓰지 않는 짧은 세션 하나를 띄워 결과 파일 접점까지 본다(모델 사용료가 조금 든다).
모델 응답이 실패하면 `transcript` 줄의 `reason`에 공급자가 보낸 문구가 나온다. 한도·잔액·연결 문제이고 `fallback_model`이 있으면 대체 모델로 한 번 다시 점검한다.
쓰는 접점 목록은 [docs/aside-interface.md](docs/aside-interface.md)에 있다.

### 설정 파일 키

자주 쓰는 키다. 전체 목록은 `src/pubfinder/config.py`의 `ASIDE_KEYS`·`RESEARCH_KEYS`에 있다.

```toml
[aside]
model = "anthropic/claude-sonnet-5-5"   # 필수
effort = "medium"                       # 필수
fallback_model = ""                     # 선택. 한도·잔액·연결 문제로 model이 거절되면 다시 돌릴 모델
executable = ""                         # 비우면 기본 설치 경로
account = "u0"
host = "local"
profile = ""
research_timeout = 1200
capture_timeout = 150
reconcile_timeout = 180
instructions_file = true                # 조사 지시 본문을 파일로 넘긴다(host가 local일 때)
autostart = false                       # true면 app_path의 Aside 앱을 한 번 시작
app_path = ""

[research]
out_dir = "pubfinder-runs"
korean_place_as_korea = true
excluded_domains = ["arxiv.org"]
run_deadline = 3600
keep_captures = false
```

## 결과 파일 칸

| 칸 | 내용 |
| --- | --- |
| `status`, `status_text` | 상태와 설명 |
| `fields.<항목>.status` | adopted 또는 not_adopted |
| `fields.<항목>.value` | 채택값 |
| `fields.<항목>.evidence` | 근거 주소·인용·위치, 인용 확인 여부 |
| `fields.<항목>.reason`, `reason_text` | 미채택 사유 코드와 설명 |
| `fields.<항목>.candidates` | 1차·2차 조사가 각각 낸 값과 근거 |
| `conflicts` | 두 조사가 서로 다른 값을 낸 항목 |
| `input_differences` | 채택값이 입력과 다른 항목 |
| `captures` | 근거 주소별 다시 열기 상태와 확인된 인용 수 |
| `warnings` | 주의 사항 |
| `run` | 실행 ID, Aside 판, 세션별 상태·오류·사용량 |
| `proof` | 판정에 쓴 자료와 판정. `verify`가 이것으로 다시 계산한다 |

판정은 입력한 제목·학술대회명이 찾은 논문과 같은지를 직접 보지 않는다. 같은 논문인지는 두 조사와 대조 세션이 정한다.
입력과 채택값이 다르면 `input_differences`와 `warnings`에 드러낸다. 저자는 한글·영문 표기 차이만으로도 다르게 나온다.

## 사람 확인 화면

사람 확인 화면(CAPTCHA 등)을 만나면 확인 체크박스를 한 번 누르도록 모든 조사 세션에 지시하고,
근거를 다시 열 때도 확인 화면에서 체크박스를 한 번 누른다. 끄는 설정은 없다.

- 누른 뒤에도 확인 화면이 남거나 401·403·429, 짧은 차단 문구 화면을 만나면 차단으로 기록하고 그 주소는 더 시도하지 않는다.
- 차단 화면 사본은 남기지 않는다.
- Aside 약관은 접근 통제와 보안 조치 우회를 금지하고, Aside 문서는 CAPTCHA를 사용자가 직접 끝내는 단계로 적는다. 이 동작을 쓰는 책임은 사용자에게 있다.
- 지금까지 측정 40건에서 확인 화면을 만난 근거는 없었다. 이 동작은 실제 Aside에서 검증되지 않았다.

## 기록과 개인정보

- 실행 기록에는 입력, 지시문, 세션 기록 사본, 두 조사 결과, 다시 열기 요약이 남는다. `pubfinder-runs/`는 git에 올라가지 않는다.
- 다시 연 원문 본문은 `--keep-captures`일 때만 남긴다.
- 파일 근거(PDF)를 받을 때 브라우저 로그인 쿠키를 싣지 않는다.
- 공인 주소가 아닌 곳(내부망 등)은 다시 열지 않는다.

## 한계

- Aside가 업데이트되면 깨질 수 있다. 쓰는 접점 가운데 여러 개가 공개 문서에 없다. 업데이트 뒤에는 `check-aside`로 점검한다.
- 다른 Aside 작업과 동시에 돌리면 세션이 엉킬 수 있다.
- 주관(측정 40건 중 2건)은 거의 채택되지 않는다. 원문에 역할 표지가 드물다.
- 같은 약칭을 쓰는 행사가 여럿이면 다른 행사를 찾아갈 수 있다. 이때는 채택되지 않는다.
- 글자 층이 없는 스캔 PDF는 인용을 확인하지 못한다(OCR 없음).
- 근거 주소는 한 건에 20개까지만 다시 연다.

## 폴더 구성

| 경로 | 내용 |
| --- | --- |
| `src/pubfinder/query.py` | 입력과 연도 읽기 |
| `src/pubfinder/prompts.py` | 1차·2차 조사 지시문 |
| `src/pubfinder/results.py` | 조사 결과 파일 읽기와 형식 검사 |
| `src/pubfinder/reconcile.py` | 대조 지시문과 답 읽기 |
| `src/pubfinder/verdict.py`, `classification.py` | 채택 판정 |
| `src/pubfinder/aside/` | Aside CLI 호출, 세션 감독, 근거 다시 열기 |
| `src/pubfinder/runner.py` | 한 건 실행 흐름 |
| `src/pubfinder/cli.py` | 명령줄 |
| `src/pubfinder/app.py`, `src/pubfinder/desktop/` | Windows 창(tkinter 화면, 창이 부르는 동작, 작업 대기열, 설정·결과 저장) |
| `scripts/build_exe.py` | Windows exe·배포 zip 만들기 |
| `tests/` | 오프라인 시험과 가짜 Aside CLI |
| `docs/` | 채택 규칙, Aside 접점, 결정 기록, 측정 결과 |

## 시험

```powershell
python -X utf8 -B -m unittest discover -s tests -t .
```

시험은 지어낸 학술대회·논문·사람 이름과 가짜 Aside CLI(`tests/fakes/fake_aside.py`)로 돈다(학회 약칭 비교 시험 하나만 실제 학회 약칭을 글자로 쓴다). 네트워크와 실제 Aside는 쓰지 않는다.

## 만든 사람

<!-- author:start -->
Seongjin Bae · ETRI Library · <sjbae7@etri.re.kr>
<!-- author:end -->
