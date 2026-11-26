"""개최국(host_country)과 국내·국제 구분(conference_scope)의 직접 근거 규칙.

두 항목은 대조 결정이 agree여도, 두 조사의 행사연도가 입력 연도와 같고 역할 표지가 있는 직접 인용이 있어야 채택한다.
근거 인용이 다시 연 원문에서 확인되는지는 `verdict`가 따로 본다.
"""

from __future__ import annotations

import re

from .fields import COUNTRY_CODE_KEY, EVIDENCE_TOOL

_COUNTRY_MARKER = (r"host\s+country|venue|location|held\s+(?:on|in|at)|take[s]?\s+place|taking\s+place|took\s+place"
                   r"|located\s+in|hosted\s+(?:in|at)|개최국|개최\s*장소|개최지|개최")
_ORGANIZER_MARKER = r"organi[sz]ed\s+by|sponsored\s+by|hosted\s+by|주최|주관|후원"
_ONLINE_ONLY = r"online[ -]only|virtual[ -]only|온라인\s*전용|복수\s*개최국"
_COUNTRY_CODE = re.compile(r"[A-Z]{2}")

SINGLE_HOST_SCOPE = "single_host_country"
DOMESTIC_ADDRESS_SCOPE = "domestic_address"
LOCATION_INFERRED_SCOPE = "location_inferred"
UNDECIDED_COUNTRY_SCOPES = {
    "online_only": "country_online_only",
    "multiple_host_countries": "country_multiple",
    "undetermined": "country_undetermined",
}
CLASSIFICATION_STATED_SCOPE = "classification_stated"
CLASSIFICATION_INFERRED_SCOPE = "classification_inferred"

_CLASSIFICATION_WORDS = {
    "international": r"\binternational\b|\bglobal\b|\bworld[ -]?wide\b|국제|세계",
    "domestic": r"\bdomestic\b|\bnational\b|국내|전국",
}
_MIN_JUDGED_QUOTE = 12
_EVIDENCE_SCOPES = {"official_event", "official_program"}


def _usable(item: dict) -> bool:
    return item.get("tool") == EVIDENCE_TOOL and item.get("scope") in _EVIDENCE_SCOPES


def _coded_country_claim(claim: dict, value: str) -> str:
    """국가명이 개최 표지 뒤나 장소 표기의 끝(`<장소>, <국가>`·`<장소> (<국가>)`)에 적힌 인용만 직접 근거다."""
    if not _COUNTRY_CODE.fullmatch(str(claim.get(COUNTRY_CODE_KEY) or "")):
        return "country_code_invalid"
    country = r"(?<!\w)" + re.escape(value) + r"(?!\w)"
    for item in claim.get("evidence") or []:
        quote = str(item.get("quote") or "")
        if not _usable(item) or not re.search(country, quote, re.I):
            continue
        if (re.search(r"\b(?:not|no)\s+" + re.escape(value) + r"\b|host\s+countries", quote, re.I)
                or re.search(_ONLINE_ONLY, quote, re.I)):
            return "country_ambiguous"
        if re.search(r"(?:" + _COUNTRY_MARKER + r")[^.!?\n]{0,200}" + country, quote, re.I):
            return ""
        if not re.search(_ORGANIZER_MARKER, quote, re.I) and re.search(
            r"(?:,\s*|\(\s*)" + re.escape(value) + r"\s*(?:\)|$|[.;|\n])", quote, re.I
        ):
            return ""
    return "country_direct_evidence_missing"


def _domestic_address_claim(claim: dict) -> str:
    """국가명 없는 한글 장소 원문으로 판단한 한국 개최. 주최 기관 주소·온라인 전용은 근거가 아니다."""
    if claim.get(COUNTRY_CODE_KEY) != "KR":
        return "country_ambiguous"
    for item in claim.get("evidence") or []:
        quote = str(item.get("quote") or "")
        if not _usable(item) or not re.search(r"[가-힣]", quote) or re.search(_ORGANIZER_MARKER, quote, re.I):
            continue
        if re.search(_ONLINE_ONLY, quote, re.I):
            return "country_ambiguous"
        return ""
    return "country_direct_evidence_missing"


def _location_inferred_claim(claim: dict) -> str:
    """국가명 없는 개최 장소 원문으로 판단한 나라. 주최 기관 주소·온라인 전용은 근거가 아니다."""
    if not _COUNTRY_CODE.fullmatch(str(claim.get(COUNTRY_CODE_KEY) or "")):
        return "country_code_invalid"
    for item in claim.get("evidence") or []:
        quote = str(item.get("quote") or "")
        if not _usable(item) or not quote.strip() or re.search(_ORGANIZER_MARKER, quote, re.I):
            continue
        if re.search(_ONLINE_ONLY, quote, re.I):
            return "country_ambiguous"
        return ""
    return "country_direct_evidence_missing"


def _classification_claim(claim: dict, value: str, scope: str) -> str:
    """국내·국제 분류. 공식 행사·프로그램 원문 인용이 있어야 하고, stated는 값과 같은 성격 낱말을 담는다."""
    for item in claim.get("evidence") or []:
        quote = str(item.get("quote") or "")
        if not _usable(item):
            continue
        if scope == CLASSIFICATION_STATED_SCOPE and not re.search(_CLASSIFICATION_WORDS[value], quote, re.I):
            continue
        if len(re.sub(r"\s", "", quote)) < _MIN_JUDGED_QUOTE:
            continue
        return ""
    return "classification_direct_evidence_missing"


def direct_claim(name: str, claim: dict, *, korean_place_as_korea: bool = True) -> str:
    """직접 근거가 있으면 빈 문자열, 없으면 미채택 사유."""
    value = str(claim.get("value") or "").strip()
    scope = str(claim.get("scope") or "")
    if name == "host_country" and scope in UNDECIDED_COUNTRY_SCOPES:
        return UNDECIDED_COUNTRY_SCOPES[scope]
    if claim.get("status") != "confirmed" or not value:
        return "research_unconfirmed"
    if name == "host_country":
        if COUNTRY_CODE_KEY not in claim:
            return "country_code_missing"
        if scope == DOMESTIC_ADDRESS_SCOPE:
            return _domestic_address_claim(claim) if korean_place_as_korea else "korean_place_rule_off"
        if scope == LOCATION_INFERRED_SCOPE:
            return _location_inferred_claim(claim)
        if scope != SINGLE_HOST_SCOPE:
            return "country_ambiguous"
        return _coded_country_claim(claim, value)
    if value in _CLASSIFICATION_WORDS and scope in {CLASSIFICATION_STATED_SCOPE, CLASSIFICATION_INFERRED_SCOPE}:
        return _classification_claim(claim, value, scope)
    return "classification_ambiguous"


def coded_countries(claims: list[dict]) -> bool:
    return all(COUNTRY_CODE_KEY in claim for claim in claims)


def independent_problem(name: str, reports: list[dict], year: str, *, korean_place_as_korea: bool = True) -> str:
    """두 보고의 행사연도가 입력 연도와 같고, 각자 직접 근거가 있고, 같은 나라·분류를 냈는가."""
    if not year or any(r.get("event", {}).get("status") != "confirmed"
                       or r.get("event", {}).get("value") != year
                       or r.get("event", {}).get("scope") != "event_year"
                       or not r.get("event", {}).get("evidence") for r in reports):
        return "event_year_unconfirmed"
    claims = [r.get("fields", {}).get(name, {}) for r in reports]
    if name == "host_country" and not coded_countries(claims):
        return "country_code_missing"
    for claim in claims:
        reason = direct_claim(name, claim, korean_place_as_korea=korean_place_as_korea)
        if reason:
            return reason
    if name == "host_country":
        # 원문 표기("Korea"·"Republic of Korea")가 달라도 같은 코드면 같은 나라다.
        return "" if claims[0][COUNTRY_CODE_KEY] == claims[1][COUNTRY_CODE_KEY] else "country_ambiguous"
    if str(claims[0]["value"]).strip().casefold() != str(claims[1]["value"]).strip().casefold():
        return "classification_ambiguous"
    return ""
