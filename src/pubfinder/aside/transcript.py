"""세션 기록(messages.jsonl) 요약과 실패 분류. 결과 파일이 없을 때 왜 없는지 가르는 데 쓴다."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")

# 공급자 사용량·속도 한도 문구.
_USAGE_LIMIT_RE = re.compile(
    r"usage[\s_-]*limit|rate[\s_-]*limit|too many requests|limit has been reached|"
    r"insufficient[\s_-]*quota|quota exceeded|out of credits?\b|\b429\b|"
    r"credit[\s_-]*balance.{0,40}too[\s_-]*low|enforced[\s_-]*spend[\s_-]*limit", re.IGNORECASE)
# 인증·공급자 연결 문제(계정에 모델이 연결되지 않은 경우 포함).
_AUTH_RE = re.compile(
    r"not signed in|unauthorized|authentication failed|\b401\b|authentication_error|permission_error|"
    r"invalid[\s_-]*(?:x-)?api[\s_-]*key|no api key|not available for this account|connect the provider|"
    r"api[\s_-]*key.{0,30}(?:missing|not (?:set|found|configured|provided)|invalid)", re.IGNORECASE)
# 그 가운데 공급자 계정의 잔액(크레딧)이 바닥난 경우. 기다려도 풀리지 않으므로 대체 모델로 바꿀 근거가 된다.
# 2026-11-25 실제 Aside 세션 기록: `400 {"type":"error","error":{"type":"invalid_request_error",
# "message":"Your credit balance is too low to access the Anthropic API. ..."}}`
_CREDIT_RE = re.compile(
    r"credit[\s_-]*balance.{0,40}too[\s_-]*low|out of credits?\b|insufficient[\s_-]*(?:quota|credits?|balance)|"
    r"purchase (?:more )?credits", re.IGNORECASE)
# 세션을 시작하지 못한 `aside exec`가 표준출력에 남기는 오류 줄(` • Error <문구>`).
_EXEC_ERROR_LINE = re.compile(r"^\s*(?:•\s*)?Error\s+(\S.*)$", re.MULTILINE)
NO_MODEL_WORK = "session_ended_without_model_work"


def clean(text: str) -> str:
    return ANSI.sub("", text or "")


def read_text(path: Path) -> str:
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def error_kind(messages: list[str]) -> str:
    text = " ".join(messages)
    if _USAGE_LIMIT_RE.search(text) or _CREDIT_RE.search(text):
        return "usage_limit"
    if _AUTH_RE.search(text):
        return "auth"
    return "provider_error" if messages else ""


def out_of_credit(summary: dict[str, Any]) -> bool:
    """공급자가 잔액 부족으로 요청을 거절해 모델이 일하지 못하고 끝난 세션인가."""
    errors = " ".join(str(item) for item in summary.get("errors") or [])
    return bool(errors) and bool(_CREDIT_RE.search(errors)) and not str(summary.get("final_text") or "").strip()


# 대체 모델로 넘어가는 까닭과 화면 문구(2026-11-25 사용자 결정: 한도·잔액·연결 문제일 때만).
REFUSAL_TEXT = {"out_of_credit": "잔액 부족", "usage_limit": "사용량 한도", "auth": "공급자 연결·인증 문제"}


def provider_refusal(summary: dict[str, Any]) -> str:
    """공급자가 요청을 거절해 모델이 답을 내지 못하고 끝난 세션이면 그 까닭(out_of_credit·usage_limit·auth).

    모델 이름 오타 같은 다른 오류는 빈 문자열이다. 설정 실수를 다른 공급자 사용료로 덮지 않게 한다.
    """
    errors = [str(item) for item in summary.get("errors") or []]
    if not errors or str(summary.get("final_text") or "").strip():
        return ""
    if out_of_credit(summary):
        return "out_of_credit"
    kind = error_kind(errors)
    return kind if kind in {"usage_limit", "auth"} else ""


def provider_message(text: str) -> str:
    """공급자 오류 줄(`400 {"error": {"message": ...}}`)에서 사람이 읽을 문구만. 꼴이 다르면 앞부분 그대로."""
    text = str(text or "")
    start = text.find("{")
    if start >= 0:
        try:
            value = json.loads(text[start:])
        except ValueError:
            value = None
        error = value.get("error") if isinstance(value, dict) else None
        message = error.get("message") if isinstance(error, dict) else None
        if isinstance(message, str) and message.strip():
            return message.strip()[:300]
    return text.strip()[:300]


def _no_model_work(summary: dict[str, Any]) -> bool:
    usage = summary.get("usage") if isinstance(summary.get("usage"), dict) else {}
    try:
        tokens = int(usage.get("total_tokens") or 0)
    except (TypeError, ValueError):
        return False
    return tokens == 0 and not summary.get("tool_calls") and not str(summary.get("final_text") or "").strip()


def is_start_failure(summary: dict[str, Any]) -> bool:
    """오류만 남기고 모델 토큰·도구 호출·최종 응답 없이 끝난 세션. 조사 시도가 아니라 기동 실패다."""
    errors = summary.get("errors") or []
    if not errors or error_kind([str(item) for item in errors]) in {"usage_limit", "auth"}:
        return False
    return _no_model_work(summary)


def summarize(path: Path | None) -> dict[str, Any]:
    """세션 기록에서 모델·사용량·오류·최종 응답·도구 호출 이름을 읽는다. 모르는 비용은 None이다."""
    summary: dict[str, Any] = {
        "models": [], "providers": [],
        "usage": {"input": 0, "output": 0, "cache_read": 0, "total_tokens": 0, "cost_total": None},
        "errors": [], "error_kind": "", "final_text": "", "tool_calls": [], "finished": False,
        "available": path is not None,
    }
    if path is None:
        return summary
    costs: list[float] = []
    for line in read_text(Path(path)).splitlines():
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        if role == "turn-lifecycle" and item.get("event") == "finished":
            summary["finished"] = True
            continue
        if role != "assistant":
            continue
        for key, bucket in (("model", "models"), ("provider", "providers")):
            value = str(item.get(key) or "")
            if value and value not in summary[bucket]:
                summary[bucket].append(value)
        usage = item.get("usage") if isinstance(item.get("usage"), dict) else {}
        for source, target in (("input", "input"), ("output", "output"), ("cacheRead", "cache_read"),
                               ("totalTokens", "total_tokens")):
            try:
                summary["usage"][target] += int(usage.get(source) or 0)
            except (TypeError, ValueError):
                pass
        cost = usage.get("cost")
        if isinstance(cost, dict) and isinstance(cost.get("total"), (int, float)):
            costs.append(float(cost["total"]))
        if item.get("stopReason") == "error" or item.get("errorMessage"):
            summary["errors"].append(str(item.get("errorMessage") or "assistant_error")[:500])
        for part in item.get("content") or []:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "toolCall":
                summary["tool_calls"].append(str(part.get("name") or "")[:100])
            elif part.get("type") == "text" and item.get("stopReason") == "stop":
                summary["final_text"] = str(part.get("text") or "")
    if costs:
        summary["usage"]["cost_total"] = round(sum(costs), 6)
    summary["error_kind"] = "start_failed" if is_start_failure(summary) else error_kind(summary["errors"])
    if summary["errors"]:
        summary["finished"] = False
    return summary


def model_verification(summary: dict[str, Any], requested: str) -> str:
    """기록의 모델 이름이 요청한 `provider/model`의 모델 부분과 같은가(matched·mismatch·unavailable)."""
    models = summary.get("models") or []
    if not models:
        return "unavailable"
    wanted = requested.rsplit("/", 1)[-1]
    return "matched" if all(str(model).rsplit("/", 1)[-1] == wanted for model in models) else "mismatch"


def adopt_exec_errors(summary: dict[str, Any], stdout_text: str, stderr_text: str = "") -> dict[str, Any]:
    """결과 파일 없이 스스로 끝난 세션의 기록에 실패 사유를 채운다. 모델이 일했거나 오류가 이미 있으면 그대로 둔다.

    오류 줄은 표준출력과 표준오류에서 찾는다. 2026-11-25 실제 Aside는 세션을 만든 뒤 쓸 수 없는 모델이면
    표준오류에 ` • Error Requested model … is not available for this account. …`을 쓰고 종료 코드 1로 끝났다.
    """
    if summary.get("errors") or not _no_model_work(summary):
        return summary
    text = clean(stdout_text)
    lines = _EXEC_ERROR_LINE.findall(text)
    if not lines and text.strip():
        # 세션 기록은 없지만 `aside exec`가 답을 표준출력에 냈다. 세션 폴더가 이미 지워진 경우로 보고 기동 실패로 두지 않는다.
        summary["stdout_answer"] = True
        return summary
    lines = lines or _EXEC_ERROR_LINE.findall(clean(stderr_text))
    summary["errors"] = [line.strip()[:500] for line in lines[:5]] or [NO_MODEL_WORK]
    summary["error_kind"] = "start_failed" if is_start_failure(summary) else error_kind(summary["errors"])
    return summary


def failure_status(record: dict, summary: dict) -> str:
    """세션 실행이 조사 결과를 낼 수 없는 상태면 그 이름, 아니면 빈 문자열."""
    if summary.get("model_verification") == "mismatch":
        # 요청한 모델이 아닌 모델이 조사했다. 사용자가 정한 모델로 한 조사가 아니므로 결과를 쓰지 않는다.
        return "research_unavailable"
    if out_of_credit(summary):
        # 화면이 충전·대체 모델 안내를 따로 보이도록 이유를 붙인다.
        return "research_unavailable:out_of_credit"
    if record.get("termination") == "launch_failed" or summary.get("error_kind") in {"usage_limit", "auth"}:
        return "research_unavailable"
    if record.get("termination") in {"timeout", "cancelled", "deadline"}:
        return "incomplete"
    if is_start_failure(summary):
        return "research_not_started"
    return ""
