"""시험용 가짜 Aside CLI. 실제 Aside와 같은 명령 모양을 받아 정해 둔 장면(scenario JSON)대로 답한다.

환경 변수
- CPF_FAKE_SCENARIO: 장면 파일 경로
- CPF_FAKE_HOME: 가짜 Aside 홈(세션 폴더가 여기에 생긴다)
- CPF_FAKE_LOG: 받은 명령을 한 줄씩 남길 파일
"""

from __future__ import annotations

import base64
import json
import os
import re
import secrets
import sys
import time
from pathlib import Path


def scenario() -> dict:
    return json.loads(Path(os.environ["CPF_FAKE_SCENARIO"]).read_text(encoding="utf-8"))


def home() -> Path:
    return Path(os.environ["CPF_FAKE_HOME"]) / "u" / "0"


def log(args: list[str]) -> None:
    """호출마다 파일 하나. 동시에 뜬 가짜 프로세스끼리 같은 파일에 덧쓰다 줄을 잃지 않게 한다."""
    folder = os.environ.get("CPF_FAKE_LOG")
    if folder:
        Path(folder).mkdir(parents=True, exist_ok=True)
        name = f"{time.time_ns():020d}-{secrets.token_hex(4)}.json"
        (Path(folder) / name).write_text(
            json.dumps([arg if len(arg) < 300 else arg[:300] + "…" for arg in args], ensure_ascii=False),
            encoding="utf-8")


def option(args: list[str], name: str) -> str:
    return args[args.index(name) + 1] if name in args else ""


def repl(args: list[str]) -> int:
    script = args[-1]
    data = scenario()
    if "cpf_ready" in script:
        if not data.get("browser_ready", True):
            print("Error: Aside Browser is not connected to the daemon")
            return 1
        print(json.dumps({"cpf_ready": True}))
        return 0
    head = re.search(r"// CPF_CAPTURE kind=(page|file) marker=([0-9a-f]+)", script)
    url = json.loads(re.search(r"const cpfUrl = (\".*?\");\n", script).group(1))
    kind, marker = head.group(1), head.group(2)
    if kind == "page":
        page = (data.get("pages") or {}).get(url)
        if page is None:
            print(json.dumps({"marker": marker, "status": "navigation_failed"}))
            return 0
        reply = {"marker": marker, "status": "ok", "final_url": page.get("final_url", url),
                 "title": page.get("title", ""), "http_status": page.get("http_status", 200),
                 "challenge": page.get("challenge", {"seen": False, "clicked": "", "resolved": True}),
                 "frame_urls": page.get("frame_urls", []),
                 "html_b64": base64.b64encode(page.get("html", page.get("text", "")).encode()).decode(),
                 "text_b64": base64.b64encode(page.get("text", "").encode()).decode()}
    else:
        item = (data.get("files") or {}).get(url)
        if item is None:
            print(json.dumps({"marker": marker, "status": "ok", "final_url": url, "http_status": 404,
                              "content_type": "text/html", "file_b64": base64.b64encode(b"<html>404</html>").decode()}))
            return 0
        if "credentials: 'omit'" not in script:
            print(json.dumps({"marker": marker, "status": "cookie_rule_missing"}))
            return 0
        reply = {"marker": marker, "status": "ok", "final_url": item.get("final_url", url),
                 "http_status": item.get("http_status", 200), "content_type": item.get("content_type", ""),
                 "file_b64": item["b64"]}
    print("log line before result")
    print(json.dumps(reply))
    return 0


CREDIT_ERROR = ('400 {"type":"error","error":{"type":"invalid_request_error","message":"Your credit balance is too '
                'low to access the Anthropic API. Please go to Plans & Billing to upgrade or purchase credits."},'
                '"request_id":"req_fake"}')


def _messages(folder: Path, *, model: str, final_text: str, errors: list[str], work: bool) -> None:
    rows = []
    if work:
        rows.append({"role": "assistant", "model": model.rsplit("/", 1)[-1], "provider": model.split("/")[0],
                     "usage": {"input": 100, "output": 20, "cacheRead": 0, "totalTokens": 120, "cost": {"total": 0.01}},
                     "stopReason": "toolUse", "content": [{"type": "toolCall", "name": "openTab",
                                                           "arguments": {"title": "x"}}]})
    for message in errors:
        rows.append({"role": "assistant", "model": model.rsplit("/", 1)[-1], "stopReason": "error",
                     "errorMessage": message, "content": []})
    if final_text:
        rows.append({"role": "assistant", "model": model.rsplit("/", 1)[-1], "provider": model.split("/")[0],
                     "usage": {"input": 10, "output": 5, "totalTokens": 15}, "stopReason": "stop",
                     "content": [{"type": "text", "text": final_text}]})
    rows.append({"role": "turn-lifecycle", "event": "finished"})
    (folder / "messages.jsonl").write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows),
                                           encoding="utf-8")


def _fill(value, values: dict):
    if isinstance(value, str):
        for key, item in values.items():
            value = value.replace("{" + key + "}", item)
        return value
    if isinstance(value, list):
        return [_fill(item, values) for item in value]
    if isinstance(value, dict):
        return {key: _fill(item, values) for key, item in value.items()}
    return value


def exec_session(args: list[str]) -> int:
    prompt = args[-1]
    data = scenario()
    found = re.search(r"run_id=([\w-]+)", prompt)
    run_id = found.group(1) if found else ""
    pointer = re.search(r'fs\.readFile\((".*?"), "utf8"\)', prompt)
    unreadable = False
    if pointer:
        # 지시 본문을 파일로 받았으면 실제 세션처럼 그 파일을 읽어 따른다. 못 읽으면 못 읽었다고 답하고 끝낸다.
        try:
            if data.get("instructions_unreadable"):
                raise OSError("scene")
            prompt = Path(json.loads(pointer.group(1))).read_text(encoding="utf-8")
        except OSError:
            unreadable = True
    model = option(args, "--model")
    path = re.search(r"fs\.writeFile로 (\./artifacts/cpf/[^ ]+/result\.json)", prompt)
    if "학술대회 발표 조사 대조" in prompt:
        role = "reconcile"
    else:
        role = path.group(1).split("/")[-2] if path else "unknown"
    plan = dict((data.get("exec") or {}).get(role) or {})
    session_id = secrets.token_hex(8)
    folder = home() / "sessions" / f"2026-01-01_{session_id}"
    (folder / "artifacts").mkdir(parents=True, exist_ok=True)
    if plan.get("no_session"):
        print(" • Error " + plan.get("stdout_error", "failed to start session"))
        return 0
    sys.stderr.write(f"created new session: {session_id}\n")
    sys.stderr.flush()
    if model in (data.get("unavailable_models") or []):
        # 2026-11-25 실제 Aside: 세션을 만든 뒤 표준오류에 오류 줄을 쓰고 종료 코드 1로 끝난다. 세션 기록은 없다.
        sys.stderr.write(f"\n • Error Requested model {model} is not available for this account. "
                         "Connect the provider or choose an available model.\n")
        return 1
    if unreadable:
        _messages(folder, model=model, final_text=f"INSTRUCTIONS_UNREADABLE {run_id}", errors=[], work=True)
        return 0
    if model in (data.get("out_of_credit_models") or []):
        # 2026-11-25 실제 세션 기록처럼 모델 이름이 붙은 오류 한 줄만 남기고 끝난다.
        _messages(folder, model=model, final_text="", errors=[CREDIT_ERROR], work=False)
        return 0
    if model in (plan.get("errors_for_models") or {}):
        # 이 역할에서 이 모델만 공급자 오류로 끝난다(한도 등).
        _messages(folder, model=model, final_text="", errors=plan["errors_for_models"][model], work=False)
        return 0
    values = {"run_id": run_id}
    for key, pattern in (("input_fingerprint", r'"input_fingerprint": "([0-9a-f]{64})"'),
                         ("review_run_id", r'"review_run_id": "([0-9a-f-]{36})"')):
        found = re.search(pattern, prompt)
        values[key] = found.group(1) if found else ""
    deadline = time.time() + float(plan.get("sleep", 0))
    stop_flag = Path(os.environ["CPF_FAKE_HOME"]) / f"stop-{session_id}"
    while time.time() < deadline:
        if stop_flag.exists():
            return 1
        time.sleep(0.1)
    result = plan.get("result")
    if result is not None and path:
        target = folder / path.group(1)[2:]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(_fill(result, values), ensure_ascii=False), encoding="utf-8")
    final_text = plan.get("final_text", f"RESEARCH_DONE {run_id}")
    if isinstance(final_text, dict):
        final_text = "대조 결과입니다.\n" + json.dumps(final_text, ensure_ascii=False)
    _messages(folder, model=plan.get("model_used", model), final_text=final_text, errors=plan.get("errors", []),
              work=plan.get("work", True))
    if plan.get("purge_session_folder"):
        # 세션이 끝나자마자 세션 폴더가 지워지는 Aside 판을 흉내 낸다.
        (folder / "messages.jsonl").unlink()
    print(final_text)
    return 0


def main() -> int:
    args = sys.argv[1:]
    log(args)
    data = scenario()
    if args == ["--version"]:
        print(data.get("version", "9.9.9-fake"))
        return 0
    if args[:2] == ["account", "status"]:
        if not data.get("app_running", True):
            print("Aside Browser isn't running. Start Aside Browser and try again.")
            return 1
        print("u0  sample@example.invalid  " + ("signed in" if data.get("signed_in", True) else "signed out"))
        return 0
    if args[:2] == ["host", "profiles"]:
        print("00000000-0000-0000-0000-000000000001  Default  connected")
        return 0
    if args[:1] == ["repl"]:
        return repl(args)
    if args[:1] == ["exec"]:
        return exec_session(args)
    if args[:2] == ["session", "stop"]:
        (Path(os.environ["CPF_FAKE_HOME"]) / f"stop-{args[-1]}").write_text("stop", encoding="utf-8")
        return 0
    if args[:2] == ["session", "list"]:
        for folder in sorted((home() / "sessions").glob("*_*")):
            print(f"{folder.name.split('_', 1)[1]} running")
        return 0
    print("unknown command", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
