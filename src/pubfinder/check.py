"""Aside 접점 점검. Aside가 갱신된 뒤 이 프로그램이 기대는 접점이 그대로인지 실제 Aside로 본다.

기본 점검은 모델을 부르지 않는다(판 읽기, 로그인 상태, 프로필, REPL 연결, 공개 화면·파일 다시 열기).
`with_exec`면 웹을 쓰지 않는 짧은 세션 하나를 띄워 세션 ID 읽기·결과 파일·세션 기록 접점까지 본다(모델 사용량을 쓴다).
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

from .aside import transcript
from .aside.capture import Capturer
from .aside.client import AsideClient
from .aside.settings import AsideSettings
from .prompts import DONE_MARKER

PAGE_URL = "https://example.com/"
PAGE_TEXT = "Example Domain"
PDF_URL = "https://www.w3.org/WAI/ER/tests/xhtml/testfiles/resources/pdf/dummy.pdf"


def check_aside(settings: AsideSettings, *, with_exec: bool = False, out_dir: Path = Path("pubfinder-runs/check"),
                client: AsideClient | None = None, resolve: Any = None) -> dict[str, Any]:
    client = client or AsideClient(settings)
    checks: list[dict[str, Any]] = []

    def record(name: str, ok: bool, detail: Any = "") -> bool:
        checks.append({"name": name, "ok": bool(ok), "detail": detail})
        return bool(ok)

    preflight = client.preflight()
    record("preflight", preflight.get("status") == "ready", preflight)
    version = preflight.get("version") or client.version()
    record("version", bool(version), version)
    if preflight.get("status") == "ready":
        profile = client.discover_profile()
        record("host_profiles", True, profile or "프로필이 하나로 정해지지 않아 기본 프로필을 씁니다")
        capturer = Capturer(client, resolve=resolve)
        page = capturer.page(PAGE_URL)
        # 이 공개 화면의 본문 문구는 바뀐 적이 있다(2026-11 확인). 제목과 본문을 함께 본다.
        seen = f"{page.get('title') or ''} {page.get('visible_text') or ''}"
        record("repl_page_capture", page.get("status") == "fetched" and PAGE_TEXT in seen,
               {key: page.get(key) for key in ("status", "final_url", "http_status", "blocked_reason", "error_type")})
        pdf = capturer.file(PDF_URL)
        record("repl_file_capture", pdf.get("status") == "fetched" and pdf.get("content_type") == "application/pdf",
               {key: pdf.get(key) for key in ("status", "final_url", "http_status", "content_type", "error_type")})
        if with_exec:
            _check_exec(client, out_dir, record)
    return {"ok": all(item["ok"] for item in checks), "aside_version": version,
            "checked_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "with_exec": with_exec, "checks": checks}


def _check_exec(client: AsideClient, out_dir: Path, record: Any) -> None:
    run_id = f"check-{uuid.uuid4().hex[:8]}"
    relpath = f"artifacts/cpf/{run_id}/check/result.json"
    prompt = (f"접점 점검(run_id={run_id}). 웹을 열지 않는다. fs.mkdir로 폴더를 만들고 fs.writeFile로 ./{relpath} 에 "
              f'UTF-8 JSON {{"ok": true, "run_id": "{run_id}"}}을 저장한 뒤 다시 읽어 확인한다. '
              "경로 문자열을 그대로 쓰고 process.cwd()·path.resolve로 다시 조립하지 않는다. "
              f"마지막 답변은 정확히 한 줄 `{DONE_MARKER} {run_id}`로 끝낸다.")
    exec_dir = Path(out_dir) / run_id
    timeout = min(300.0, client.settings.research_timeout)
    # 조사와 같은 길로 돌린다. 한도·잔액·연결 문제로 거절되면 설정의 대체 모델로 한 번 다시 한다.
    result, collected = client.run_session(prompt, exec_dir, relpath, timeout=timeout)
    record("exec_session_id", bool(result.get("session_id")), {key: result.get(key) for key in
                                                                ("session_id", "model", "termination", "exit_code")})
    if client.fallback:
        fallback = client.fallback
        why = transcript.REFUSAL_TEXT.get(fallback.get("reason", ""), "공급자 거절")
        record("model_fallback", True, {"reason": f"{fallback['from']} {why}({fallback['message']}) → 대체 모델 "
                                                  f"{fallback['to']}로 점검했습니다. 조사도 같은 경우 대체 모델로 바꿉니다."})
    record("session_folder", bool(collected.get("session_folder")), collected.get("session_folder"))
    summary = collected.get("transcript") or {}
    answered = bool(summary.get("models")) and DONE_MARKER in str(summary.get("final_text"))
    kind, reason = ("", "") if answered else _problem(result, summary, timeout)
    ok = False
    if collected.get("result_file"):
        try:
            value = json.loads(Path(collected["result_file"]).read_text(encoding="utf-8-sig"))
            ok = value.get("run_id") == run_id
        except (OSError, ValueError):
            ok = False
    if ok:
        record("result_file", True, collected.get("result_source"))
    elif kind.startswith("model_"):
        record("result_file", False, {"kind": kind, "reason": "모델 응답이 실패해 결과 파일이 만들어지지 않았습니다"})
    else:
        record("result_file", False, {"kind": "result_missing", "reason": "세션이 지시한 결과 파일을 남기지 않았습니다"})
    detail = {key: summary.get(key) for key in ("models", "model_verification", "errors", "error_kind")}
    record("transcript", answered, {**detail, "kind": kind, "reason": reason} if kind else detail)
    status = client.session_status(str(result.get("session_id") or ""))
    record("session_list", status not in {"unknown"}, status)


def _problem(record: dict, summary: dict, timeout: float) -> tuple[str, str]:
    """모델 응답 점검이 실패한 까닭(화면 안내를 고를 종류, 화면에 보일 한 줄)."""
    errors = summary.get("errors") or []
    message = transcript.provider_message(str(errors[0])) if errors else ""
    if transcript.out_of_credit(summary):
        return "model_out_of_credit", f"공급자 잔액 부족: {message}"
    if summary.get("error_kind") == "usage_limit":
        return "model_usage_limit", f"공급자 사용량 한도: {message}"
    if summary.get("error_kind") == "auth":
        return "model_auth", f"공급자 연결·인증 오류: {message}"
    if errors:
        return "model_error", f"모델 오류: {message}"
    if record.get("termination") in {"timeout", "cancelled"}:
        return "incomplete", f"점검 세션이 {int(timeout)}초 안에 끝나지 않았습니다"
    if not summary.get("available"):
        return "session_record_missing", "Aside 세션 기록(messages.jsonl)을 찾지 못했습니다"
    return "done_marker_missing", "모델이 점검 지시를 끝까지 따르지 않았습니다(끝 표시 없음)"
