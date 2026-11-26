"""조사 세션이 저장한 결과 JSON을 읽고 형식을 확인한다.

입력 결속(run_id·입력 지문·2차 확인 ID)과 관찰의 꼴은 엄격하게 보고, 판정에 쓰지 않는 칸(모르는 최상위 키,
지시하지 않은 항목, 틀린 탐색 기록)은 버리고 `notes`에 남긴다.
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any, Iterable
from urllib.parse import urlsplit

from .fields import COUNTRY_CODE_KEY, DATE_FIELDS, FIELDS
from .prompts import PRIMARY_SCHEMA, REVIEW_SCHEMA
from .sources import DEFAULT_EXCLUDED_DOMAINS, source_allowed

OUTCOMES = {"found", "not_found", "needs_review", "blocked"}
CLAIM_STATUSES = {"confirmed", "unknown", "conflict"}
EVIDENCE_KEYS = {"url", "quote", "locator", "observation", "tool", "scope"}
# 판정에 쓰지 않는 메모 칸. 비어 있어도 보고 전체를 버리지 않는다(2026-11-15 실제 측정에서 빈 observation 한 칸으로
# 2차 결과 전체가 버려졌다). 나머지 칸은 비어 있으면 안 된다.
OPTIONAL_EVIDENCE_TEXT = {"locator", "observation"}
TRAIL_PURPOSES = {"identify_event", "program", "date", "presentations", "session"}
TRAIL_RESULTS = {"confirmed", "no_match", "access_failed", "unknown"}
MAX_TEXT = 4000
MAX_EVIDENCE_TEXT = 16000
MAX_EVIDENCE = 8
MAX_RESULT_BYTES = 5 * 1024 * 1024


class ResultError(ValueError):
    """결과 파일을 판정에 쓸 수 없다. 메시지는 짧은 사유 코드다."""


def _load(raw: bytes) -> dict[str, Any]:
    if len(raw) > MAX_RESULT_BYTES:
        raise ResultError("result_too_large")
    try:
        value = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ResultError("result_json_invalid") from exc
    if not isinstance(value, dict):
        raise ResultError("result_object_required")
    return value


def check_claim(name: str, claim: object, excluded: Iterable[str]) -> dict:
    """관찰 하나의 꼴을 확인해 그대로 돌려준다. 틀리면 ResultError."""
    coded = name == "host_country" and isinstance(claim, dict) and COUNTRY_CODE_KEY in claim
    keys = {"status", "value", "scope", "reason", "evidence", *([COUNTRY_CODE_KEY] if coded else [])}
    if not isinstance(claim, dict) or set(claim) != keys:
        raise ResultError(f"result_claim_keys:{name}")
    if claim["status"] not in CLAIM_STATUSES:
        raise ResultError(f"result_claim_status:{name}")
    if any(not isinstance(claim[k], str) or len(claim[k]) > MAX_TEXT for k in ("value", "scope", "reason")):
        raise ResultError(f"result_claim_text:{name}")
    if coded and (not isinstance(claim[COUNTRY_CODE_KEY], str) or len(claim[COUNTRY_CODE_KEY]) > 8):
        raise ResultError(f"result_country_code:{name}")
    if not isinstance(claim["evidence"], list) or len(claim["evidence"]) > MAX_EVIDENCE:
        raise ResultError(f"result_claim_evidence:{name}")
    if name in DATE_FIELDS and claim["value"]:
        try:
            if date.fromisoformat(claim["value"]).isoformat() != claim["value"]:
                raise ValueError
        except ValueError as exc:
            raise ResultError(f"result_date_invalid:{name}") from exc
    for evidence in claim["evidence"]:
        if (not isinstance(evidence, dict) or set(evidence) != EVIDENCE_KEYS
                or any(not isinstance(v, str) or len(v) > MAX_EVIDENCE_TEXT
                       or (not v.strip() and k not in OPTIONAL_EVIDENCE_TEXT) for k, v in evidence.items())):
            raise ResultError(f"result_evidence_shape:{name}")
        if not source_allowed(evidence["url"], excluded):
            raise ResultError(f"result_evidence_url_not_allowed:{name}")
    return claim


def _observations(value: dict, excluded: Iterable[str], notes: list[str]) -> dict:
    excluded = tuple(excluded)
    fields = value.get("fields")
    if not isinstance(fields, dict):
        raise ResultError("result_fields_required")
    ignored = sorted(set(fields) - set(FIELDS))
    if ignored:
        notes.append("ignored_fields:" + ",".join(ignored)[:300])
    country = fields.get("host_country")
    if isinstance(country, dict) and COUNTRY_CODE_KEY in country:
        # 코드 형식이 틀려도 보고 전체를 버리지 않게 문자열로만 다듬는다. 형식 판정은 채택 단계가 한다.
        code = country[COUNTRY_CODE_KEY]
        country[COUNTRY_CODE_KEY] = "" if code is None else str(code).strip().upper()[:8]
    checked = {name: check_claim(name, fields[name], excluded) for name in FIELDS if name in fields}
    return {
        "identity": check_claim("identity", value.get("identity"), excluded),
        "event": check_claim("event", value.get("event"), excluded),
        "fields": checked,
        "limitations": [str(item)[:500] for item in (value.get("limitations") or [])][:20]
        if isinstance(value.get("limitations"), list) else [],
    }


def _trail(value: object, notes: list[str]) -> list[dict]:
    rows = value if isinstance(value, list) else []
    kept = []
    for item in rows[:30]:
        if not isinstance(item, dict):
            continue
        row = {key: str(item.get(key) or "")[:4000] for key in
               ("query", "url", "purpose", "result", "controls", "observation")}
        url = urlsplit(row["url"]) if row["url"] else None
        if ((not row["query"].strip() and not row["url"].strip())
                or (url is not None and (url.scheme not in {"http", "https"} or not url.hostname))
                or row["purpose"] not in TRAIL_PURPOSES or row["result"] not in TRAIL_RESULTS):
            continue
        kept.append(row)
    if len(kept) != len(rows):
        notes.append(f"trail_rows_dropped:{len(rows) - len(kept)}")
    return kept


def _expect(value: dict, expected: dict[str, str]) -> None:
    for key, wanted in expected.items():
        if value.get(key) != wanted:
            raise ResultError(f"result_{key}_mismatch")


def parse_primary_result(raw: bytes, *, run_id: str, input_fingerprint: str,
                         excluded: Iterable[str] = DEFAULT_EXCLUDED_DOMAINS) -> dict[str, Any]:
    value = _load(raw)
    _expect(value, {"schema_version": PRIMARY_SCHEMA, "run_id": run_id, "input_fingerprint": input_fingerprint})
    notes: list[str] = []
    unknown = sorted(set(value) - {"schema_version", "run_id", "input_fingerprint", "outcome", "reason",
                                   "limitations", "trail", "identity", "event", "fields"})
    if unknown:
        notes.append("ignored_keys:" + ",".join(unknown)[:300])
    if value.get("outcome") not in OUTCOMES:
        raise ResultError("result_outcome_invalid")
    return {
        "outcome": value["outcome"],
        "reason": str(value.get("reason") or "")[:MAX_TEXT],
        "trail": _trail(value.get("trail"), notes),
        "observations": _observations(value, excluded, notes),
        "notes": notes,
    }


def parse_review_result(raw: bytes, *, run_id: str, review_run_id: str,
                        excluded: Iterable[str] = DEFAULT_EXCLUDED_DOMAINS) -> dict[str, Any]:
    value = _load(raw)
    _expect(value, {"schema_version": REVIEW_SCHEMA, "run_id": run_id, "review_run_id": review_run_id})
    notes: list[str] = []
    unknown = sorted(set(value) - {"schema_version", "run_id", "review_run_id", "limitations",
                                   "identity", "event", "fields"})
    if unknown:
        notes.append("ignored_keys:" + ",".join(unknown)[:300])
    return {"observations": _observations(value, excluded, notes), "notes": notes}


def has_evidence(observations: dict[str, Any]) -> bool:
    """원문 근거를 하나라도 남겼는지. 하나도 없으면 원문을 열지 않고 끝난 조사다."""
    claims = [observations.get("identity"), observations.get("event"), *(observations.get("fields") or {}).values()]
    return any(isinstance(claim, dict) and claim.get("evidence") for claim in claims)


def evidence_urls(*observations: dict) -> list[str]:
    """보고들의 근거 주소를 나온 순서대로, 겹치지 않게."""
    urls = []
    for item in observations:
        claims = [item.get("identity") or {}, item.get("event") or {}, *(item.get("fields") or {}).values()]
        for claim in claims:
            for evidence in claim.get("evidence") or []:
                if isinstance(evidence, dict) and evidence.get("url"):
                    urls.append(str(evidence["url"]))
    return list(dict.fromkeys(urls))
