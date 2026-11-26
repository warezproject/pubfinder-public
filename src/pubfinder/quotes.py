"""두 조사의 인용이 프로그램이 다시 연 원문에 있는지 확인하고 요약한다. 원문 자체는 요약에 넣지 않는다."""

from __future__ import annotations

import re
from typing import Iterable

from .textutil import fingerprint, text_key

_MIN_QUOTE_SEGMENT = 8
_MIN_QUOTE_LONGEST = 12


def quote_found(quote: str, haystack_key: str) -> bool:
    """생략 기호(... 또는 …)로 나뉜 인용 조각이 모두 원문에 있어야 한다.

    어느 프로그램 페이지에나 있는 짧은 조각(월 이름·세션 번호 등) 조합을 인정하지 않도록 조각마다 길이 하한을 둔다.
    비교는 숫자·영문·한글만 남긴 글(`text_key`)로 한다.
    """
    segments = [text_key(part) for part in re.split(r"\.\.\.|…", quote or "")]
    segments = [segment for segment in segments if segment]
    return (
        bool(segments)
        and all(len(segment) >= _MIN_QUOTE_SEGMENT for segment in segments)
        and max(map(len, segments)) >= _MIN_QUOTE_LONGEST
        and all(segment in haystack_key for segment in segments)
    )


def claims_of(report: dict) -> list[dict]:
    fields = report.get("fields")
    values = list(fields.values()) if isinstance(fields, dict) else []
    return [report.get("identity") or {}, report.get("event") or {}, *values]


def capture_summary(reports: Iterable[dict], captures: dict[str, dict]) -> list[dict]:
    """근거 주소마다 다시 열기 상태와 원문에서 확인된 인용 지문(sha256)만 남긴다."""
    quotes: dict[str, set[str]] = {}
    for report in reports:
        for claim in claims_of(report):
            for evidence in claim.get("evidence") or []:
                if isinstance(evidence, dict) and evidence.get("url"):
                    quotes.setdefault(evidence["url"], set()).add(str(evidence.get("quote") or ""))
    items = []
    for url in sorted(quotes):
        capture = captures.get(url) or {}
        status = str(capture.get("status") or "not_captured")
        haystack = text_key(" ".join(str(capture.get(key) or "") for key in ("visible_text", "text")))
        verified = sorted(fingerprint(q) for q in quotes[url] if quote_found(q, haystack)) if status == "fetched" else []
        item = {
            "url": url,
            "status": status,
            "kind": str(capture.get("kind") or ""),
            "final_url": str(capture.get("final_url") or ""),
            "http_status": capture.get("http_status"),
            "content_type": str(capture.get("content_type") or ""),
            "sha256": str(capture.get("sha256") or ""),
            "collected_at": capture.get("collected_at"),
            "quote_count": len(quotes[url]),
            "verified_quotes": verified,
        }
        for key in ("blocked_reason", "error_type", "challenge"):
            if capture.get(key):
                item[key] = capture[key]
        items.append(item)
    return items
