"""판정을 사용자에게 돌려줄 결과 JSON과 한국어 요약으로 바꾼다."""

from __future__ import annotations

import re
from typing import Any

from .fields import COUNTRY_CODE_KEY, FIELDS, LABELS, group
from .query import Query
from .textutil import fingerprint, normalized, text_key

RESULT_SCHEMA = "cpf-result-v1"

REASON_TEXT = {
    "reconcile_agree": "채택",
    "outcome_not_found": "1차 조사가 논문을 찾지 못함(not_found)",
    "one_sided": "한쪽 조사만 값을 냄",
    "unconfirmed": "두 조사 모두 확인하지 못함",
    "reconcile_disagree": "두 조사의 값이 다름(대조 결정)",
    "reconcile_primary_only": "1차 조사만 값을 냄(대조 결정)",
    "reconcile_review_only": "2차 조사만 값을 냄(대조 결정)",
    "reconcile_neither": "대조가 두 값을 인정하지 않음",
    "identity_not_agreed": "같은 논문이라는 합의가 없음",
    "event_year_unconfirmed": "행사연도가 입력 연도와 같다는 확인이 없음(연도 입력 권장)",
    "capture_unverified": "인용이 다시 연 원문에서 확인되지 않음",
    "values_differ": "두 조사의 값이 다름",
    "core_not_verified": "핵심(같은 논문·행사연도·발표일·제목)이 확인되지 않아 채택하지 않음",
    "country_code_missing": "국가 코드가 없음",
    "country_code_invalid": "국가 코드 꼴이 틀림",
    "country_ambiguous": "개최국이 분명하지 않음",
    "country_direct_evidence_missing": "개최국을 직접 밝힌 인용이 없음",
    "country_online_only": "온라인 전용 행사",
    "country_multiple": "개최국이 여럿",
    "country_undetermined": "개최국 미정",
    "korean_place_rule_off": "한글 장소를 한국으로 보는 규칙이 꺼져 있음",
    "classification_direct_evidence_missing": "국내·국제 성격을 밝힌 인용이 없음",
    "classification_ambiguous": "국내·국제 구분이 분명하지 않음",
    "research_unconfirmed": "조사가 확인하지 못함",
}

STATUS_TEXT = {
    "verified": "같은 논문의 발표일·제목까지 확인됨",
    "partial": "일부 항목만 확인됨",
    "not_verified": "채택된 항목 없음",
    "not_found": "1차 조사가 논문을 찾지 못함",
    "blocked": "근거 접근이 막혀 확인하지 못함",
    "failed": "조사를 끝내지 못함",
}


def _candidate(claim: dict) -> dict:
    if not claim:
        return {"status": "missing", "value": ""}
    view: dict[str, Any] = {
        "status": claim.get("status", ""),
        "value": claim.get("value", ""),
        "scope": claim.get("scope", ""),
        "reason": claim.get("reason", ""),
        "evidence": [{key: item.get(key, "") for key in ("url", "quote", "locator", "scope")}
                     for item in claim.get("evidence") or []],
    }
    if COUNTRY_CODE_KEY in claim:
        view[COUNTRY_CODE_KEY] = claim[COUNTRY_CODE_KEY]
    return view


def _input_value(query: Query, name: str) -> str:
    source = query.source_input()
    if name in {"title_ko", "title_en"}:
        return source[name]
    if name == "venue_name":
        return query.venue
    if name == "observed_authors":
        return ", ".join(query.authors)
    return ""


_YEAR = re.compile(r"(?<!\d)(?:19|20)\d{2}(?!\d)")
_INITIAL_SKIP = {"a", "an", "and", "at", "for", "in", "of", "on", "the", "to"}


def _acronym_of(given: str, found: str) -> bool:
    """`ICASSP 2026`처럼 약칭 한 낱말(3자 이상)과 연도로 넣은 입력이 정식 이름 낱말 머리글자와 이어서 맞는지 본다."""
    given_years, found_years = set(_YEAR.findall(given)), set(_YEAR.findall(found))
    if given_years and found_years and not given_years & found_years:
        return False
    tokens = re.findall(r"[A-Za-z]+", _YEAR.sub(" ", given))
    if len(tokens) != 1 or len(tokens[0]) < 3:
        return False
    initials = "".join(word[0] for word in re.findall(r"[A-Za-z]+", found) if word.lower() not in _INITIAL_SKIP)
    return tokens[0].lower() in initials.lower()


def _same(name: str, given: str, found: str) -> bool:
    if name == "venue_name":
        # 행사명은 약칭(`IMAGCONF 2026`, `ICASSP 2026`)으로 넣는 일이 많다. 한쪽이 다른 쪽 안에 들어 있거나
        # 약칭이 정식 이름 머리글자와 맞으면 같은 행사명으로 본다.
        left, right = text_key(given), text_key(found)
        return bool(left and right and (left in right or right in left)) or _acronym_of(given, found)
    return normalized(given) == normalized(found)


def input_comparison(query: Query, verdict: dict) -> dict[str, dict]:
    """채택값과 입력이 다른 곳. 판정은 입력과 찾은 논문이 같은지를 직접 보지 않으므로 여기서 따로 보인다."""
    rows = {}
    for name in ("title_ko", "title_en", "venue_name", "observed_authors"):
        entry = (verdict.get("fields") or {}).get(name) or {}
        given = _input_value(query, name)
        if not entry.get("adopted") or not given:
            continue
        rows[name] = {"input": given, "found": entry["value"], "same": _same(name, given, entry["value"])}
    return rows


def overall_status(verdict: dict, outcome: str) -> str:
    if verdict.get("core_verified"):
        return "verified"
    if verdict.get("accepted"):
        return "partial"
    if outcome in {"not_found", "blocked"}:
        return outcome
    return "not_verified"


def build_result(query: Query, *, bundle: dict, verdict: dict, run: dict, captures: list[dict],
                 warnings: list[str]) -> dict:
    fields = {}
    for name in FIELDS:
        entry = (verdict.get("fields") or {}).get(name) or {}
        claims = [((bundle.get(role) or {}).get("fields") or {}).get(name, {}) for role in ("primary", "review")]
        rule = str(entry.get("rule") or "")
        item: dict[str, Any] = {
            "label": LABELS[name],
            "group": group(name),
            "status": "adopted" if entry.get("adopted") else "not_adopted",
            "value": entry.get("value", ""),
            "reason": "" if entry.get("adopted") else rule,
            "reason_text": "" if entry.get("adopted") else REASON_TEXT.get(rule, rule),
            "evidence": {**{key: (entry.get("evidence") or {}).get(key, "") for key in
                            ("url", "quote", "locator", "scope")},
                         "quote_verified": bool(entry.get("quote_verified"))} if entry.get("adopted") else {},
            "candidates": {"primary": _candidate(claims[0]), "review": _candidate(claims[1])},
        }
        if COUNTRY_CODE_KEY in entry:
            item[COUNTRY_CODE_KEY] = entry[COUNTRY_CODE_KEY]
        if "reconcile" in entry:
            item["reconcile"] = entry["reconcile"]
        fields[name] = item
    comparison = input_comparison(query, verdict)
    differences = [name for name, row in comparison.items() if not row["same"]]
    notes = list(warnings)
    if differences:
        notes.append("입력과 다르게 채택된 항목: " + ", ".join(LABELS[name] for name in differences)
                     + ". 같은 논문인지 직접 확인하세요(저자는 한글·영문 표기 차이일 수 있습니다).")
    if not query.year:
        notes.append("연도가 없어 개최국·국내외 구분은 채택되지 않습니다. --year 입력을 권합니다.")
    status = overall_status(verdict, bundle.get("outcome", ""))
    proof = {"bundle": bundle, "verdict": verdict,
             "digest": fingerprint({"bundle": bundle, "verdict": verdict})}
    return {
        "schema_version": RESULT_SCHEMA,
        "status": status,
        "status_text": STATUS_TEXT[status],
        "query": {"title": query.title, "venue": query.venue, "authors": list(query.authors),
                  "year": query.year, "year_source": query.year_source, "year_input": query.year_input},
        "summary": {
            "policy": verdict.get("policy"),
            "primary_outcome": bundle.get("outcome"),
            "identity_agreed": bool(verdict.get("identity_agreed")),
            "core_verified": bool(verdict.get("core_verified")),
            "adopted": sorted(verdict.get("accepted") or {}),
            "adopted_count": len(verdict.get("accepted") or {}),
        },
        "fields": fields,
        "conflicts": verdict.get("conflicts") or {},
        "input_comparison": comparison,
        "input_differences": differences,
        "warnings": notes,
        "captures": [{key: item.get(key) for key in ("url", "status", "kind", "final_url", "http_status",
                                                     "quote_count", "blocked_reason", "error_type")}
                     | {"verified_quote_count": len(item.get("verified_quotes") or [])} for item in captures],
        "run": run,
        "proof": proof,
    }


def failure_result(query: Query, *, reason: str, run: dict, warnings: list[str] | None = None,
                   partial: dict | None = None) -> dict:
    """판정까지 가지 못한 실행. 받은 1차 결과가 있으면 참고로 싣고 채택하지 않는다."""
    return {
        "schema_version": RESULT_SCHEMA,
        "status": "failed",
        "status_text": STATUS_TEXT["failed"],
        "reason": reason,
        "query": {"title": query.title, "venue": query.venue, "authors": list(query.authors),
                  "year": query.year, "year_source": query.year_source, "year_input": query.year_input},
        "warnings": list(warnings or []),
        "partial": partial or {},
        "run": run,
    }


def render_text(result: dict) -> str:
    """터미널에 보일 한국어 요약."""
    query = result.get("query") or {}
    lines = [
        f"상태: {result.get('status')} ({result.get('status_text', '')})",
        f"입력: {query.get('title', '')} / {query.get('venue', '')} / {', '.join(query.get('authors') or [])}"
        + (f" / {query.get('year')}" if query.get("year") else ""),
    ]
    if result.get("status") == "failed":
        lines.append(f"사유: {result.get('reason', '')}")
    else:
        summary = result.get("summary") or {}
        lines.append(f"판정 정책: {summary.get('policy')} · 같은 논문 합의: {'예' if summary.get('identity_agreed') else '아니오'}"
                     f" · 채택 {summary.get('adopted_count', 0)}/{len(FIELDS)}")
        for name in FIELDS:
            item = (result.get("fields") or {}).get(name) or {}
            if item.get("status") == "adopted":
                code = f" [{item[COUNTRY_CODE_KEY]}]" if item.get(COUNTRY_CODE_KEY) else ""
                verified = "인용 확인" if (item.get("evidence") or {}).get("quote_verified") else "인용 미확인"
                lines.append(f"  ✓ {item['label']}: {item['value']}{code} ({verified}) {item['evidence'].get('url', '')}")
            else:
                lines.append(f"  · {item.get('label', name)}: 미채택 - {item.get('reason_text', '')}")
    for note in result.get("warnings") or []:
        lines.append(f"주의: {note}")
    return "\n".join(lines)
