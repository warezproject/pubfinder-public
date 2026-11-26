"""채택 판정. 두 조사 보고, 다시 연 원문의 인용 확인 요약, 대조 결정을 묶은 `bundle` 하나만 보고 정한다.

정책 두 가지
- `cpf-reconcile-v1`(대조 결정이 있을 때): 두 조사 모두 값이 있고 대조 결정이 agree면 1차 값을 채택한다.
  논문 단위 6개는 같은 논문 합의(대조 identity agree, 두 조사 모두 same_paper)가 더 필요하다.
  개최국·국내외 구분은 두 조사의 행사연도가 입력 연도와 같고, 역할 표지가 있는 직접 인용이 다시 연 원문에서
  확인돼야 한다. 나머지 항목의 인용 확인은 `quote_verified`로 기록만 한다.
- `cpf-string-agreement-v1`(대조 답을 못 얻었을 때): 두 조사 모두 confirmed이고 정규화한 값이 같고 인용이 다시 연
  원문에서 확인돼야 한다. 같은 논문·행사연도(입력 연도와 같음)·개별 발표일·제목이 모두 확인되지 않으면 아무것도
  채택하지 않는다.

같은 bundle이면 언제 다시 계산해도 같은 결과다(`verify_result`).
"""

from __future__ import annotations

import re
from typing import Any

from .classification import UNDECIDED_COUNTRY_SCOPES, coded_countries, direct_claim, independent_problem
from .fields import CLASSIFICATION_FIELDS, COUNTRY_CODE_KEY, FIELDS, PAPER_FIELDS
from .reconcile import parse_reconcile
from .results import OUTCOMES, ResultError, check_claim
from .textutil import fingerprint, normalized

BUNDLE_SCHEMA = "cpf-bundle-v1"
RECONCILE_POLICY = "cpf-reconcile-v1"
STRING_POLICY = "cpf-string-agreement-v1"


class BundleError(ValueError):
    """판정 묶음의 결속이 깨졌다."""


def report(role: str, observations: dict, *, run_id: str, session_id: str, review_run_id: str = "") -> dict:
    """조사 세션 하나의 보고. 관찰은 `results.parse_*_result`가 확인한 것이다."""
    value = {
        "role": role,
        "run_id": run_id,
        "session_id": session_id,
        "identity": observations["identity"],
        "event": observations["event"],
        "fields": dict(observations["fields"]),
        "limitations": list(observations.get("limitations") or []),
    }
    if role == "review":
        value["review_run_id"] = review_run_id
    return value


def make_bundle(source: dict, *, outcome: str, review_run_id: str, primary: dict, review: dict,
                captures: list[dict], reconcile: dict | None, korean_place_as_korea: bool,
                excluded_domains: tuple[str, ...]) -> dict:
    return {
        "schema": BUNDLE_SCHEMA,
        "input": source,
        "input_fingerprint": fingerprint(source),
        "outcome": outcome,
        "review_run_id": review_run_id,
        "settings": {"korean_place_as_korea": korean_place_as_korea, "excluded_domains": list(excluded_domains)},
        "primary": primary,
        "review": review,
        "captures": captures,
        "reconcile": reconcile,
    }


def check_bundle(bundle: dict) -> None:
    if not isinstance(bundle, dict) or bundle.get("schema") != BUNDLE_SCHEMA:
        raise BundleError("bundle_schema_mismatch")
    if fingerprint(bundle.get("input") or {}) != bundle.get("input_fingerprint"):
        raise BundleError("bundle_input_changed")
    if bundle.get("outcome") not in OUTCOMES:
        raise BundleError("bundle_outcome_invalid")
    primary, review = bundle.get("primary") or {}, bundle.get("review") or {}
    if primary.get("role") != "primary" or review.get("role") != "review":
        raise BundleError("bundle_roles_invalid")
    if not bundle.get("review_run_id") or review.get("review_run_id") != bundle["review_run_id"]:
        raise BundleError("bundle_review_run_mismatch")
    sessions = [str(report.get("session_id") or "") for report in (primary, review)]
    if not all(sessions) or sessions[0] == sessions[1]:
        raise BundleError("bundle_sessions_not_independent")
    excluded = tuple((bundle.get("settings") or {}).get("excluded_domains") or ())
    try:
        for report in (primary, review):
            check_claim("identity", report.get("identity"), excluded)
            check_claim("event", report.get("event"), excluded)
            fields = report.get("fields")
            if not isinstance(fields, dict) or set(fields) - set(FIELDS):
                raise BundleError("bundle_fields_invalid")
            for name, claim in fields.items():
                check_claim(name, claim, excluded)
    except ResultError as exc:
        raise BundleError(f"bundle_claim_invalid:{exc}") from exc
    if not isinstance(bundle.get("captures"), list):
        raise BundleError("bundle_captures_invalid")
    reconcile = bundle.get("reconcile")
    if reconcile is not None:
        try:
            if parse_reconcile(reconcile) != reconcile:
                raise BundleError("bundle_reconcile_not_normalized")
        except ValueError as exc:
            raise BundleError(f"bundle_reconcile_invalid:{exc}") from exc


def _korean_place(bundle: dict) -> bool:
    return bool((bundle.get("settings") or {}).get("korean_place_as_korea", True))


def _fetched(bundle: dict) -> dict[str, dict]:
    return {item.get("url"): item for item in bundle.get("captures") or []
            if isinstance(item, dict) and item.get("status") == "fetched"}


def _capture_verified(bundle: dict, claims: list[dict]) -> bool:
    """근거 하나 이상이 다시 연 원문에서 같은 주소·인용으로 확인되면 참."""
    captures = _fetched(bundle)
    for claim in claims:
        for evidence in claim.get("evidence") or []:
            item = captures.get(evidence.get("url"))
            if item and fingerprint(evidence.get("quote", "")) in set(item.get("verified_quotes") or []):
                return True
    return False


def _direct_captured_evidence(bundle: dict, name: str, claim: dict) -> list[dict]:
    """개최국·구분의 직접 인용 가운데 다시 연 원문에서 확인된 근거만."""
    korean = _korean_place(bundle)
    return [item for item in claim.get("evidence") or []
            if not direct_claim(name, {**claim, "evidence": [item]}, korean_place_as_korea=korean)
            and _capture_verified(bundle, [{**claim, "evidence": [item]}])]


def _confirmed(claim: dict, scope: str = "") -> bool:
    evidence_scopes = {"official_program", "official_paper"}
    if scope not in {"paper_identity", "individual_presentation"}:
        evidence_scopes.add("official_event")
    return bool(
        claim.get("status") == "confirmed"
        and claim.get("value")
        and claim.get("evidence")
        and (not scope or claim.get("scope") == scope)
        and any(e.get("scope") in evidence_scopes for e in claim["evidence"])
    )


def _venue_key(value: str, year: str) -> str | None:
    """확인된 행사연도만 겹침을 지운다. 다른 연도·회차·괄호는 남긴다."""
    if any(number != year for number in re.findall(r"(?<!\d)\d{4}(?!\d)", value)):
        return None
    value = re.sub(r"^the\s+", "", value)
    matches = list(re.finditer(r"(?<!\w)" + re.escape(year) + r"(?!\w)", value))
    for match in reversed(matches[:-1]):
        value = value[:match.start()] + value[match.end():]
    return normalized(value)


def _session_parts(value: str) -> tuple[str, dict[str, str]] | None:
    """끝의 명시적 paper 번호 설명만 떼어 낸다. 다른 괄호는 세션 이름의 일부다."""
    match = re.fullmatch(r"(.+?)\s+\(paper\s+#?(\d+)(?:\s*/\s*([a-z][a-z0-9-]*-\d+))?\)", value)
    if not match:
        return value, {}
    base, number, code = match.groups()
    identifiers = {"paper_number": number}
    if code:
        if code.rsplit("-", 1)[0] != base.split()[0].rstrip(":."):
            return None
        identifiers["session_paper_code"] = code
    return base, identifiers


def _agreement(name: str, first: str, second: str, year: str) -> tuple[bool, str]:
    """문자열 일치 정책의 비교. 원문은 바꾸지 않고 항목별로 제한된 규칙만 쓴다."""
    first, second = normalized(first), normalized(second)
    if first == second:
        return True, "normalized_exact"
    if name == "venue_name" and year:
        keys = [_venue_key(value, year) for value in (first, second)]
        if keys[0] and keys[0] == keys[1]:
            without_article = [re.sub(r"^the\s+", "", value) for value in (first, second)]
            return True, "venue_leading_the" if without_article[0] == without_article[1] else "venue_repeated_year"
    if name == "session":
        parts = [_session_parts(value) for value in (first, second)]
        if parts[0] and parts[1] and parts[0][0] == parts[1][0]:
            identifiers = [part[1] for part in parts if part]
            common = identifiers[0].keys() & identifiers[1].keys()
            if all(identifiers[0][key] == identifiers[1][key] for key in common):
                return True, "session_paper_identifier_suffix"
    return False, "values_differ"


def _undecided_country(name: str, claims: list[dict], korean: bool) -> str:
    if name != "host_country":
        return ""
    for claim in claims:
        if claim.get("scope") in UNDECIDED_COUNTRY_SCOPES:
            return direct_claim(name, claim, korean_place_as_korea=korean)
    return ""


def _field_entry(bundle: dict, name: str, claims: list[dict], adopted: bool, rule: str,
                 evidence: dict | None = None) -> dict:
    entry: dict[str, Any] = {
        "adopted": adopted,
        "value": str(claims[0].get("value") or "") if adopted else "",
        "rule": rule,
        "evidence": evidence or {},
        "quote_verified": _capture_verified(bundle, claims),
    }
    if adopted and name == "host_country" and coded_countries(claims):
        entry[COUNTRY_CODE_KEY] = claims[0][COUNTRY_CODE_KEY]
    return entry


def _reconciled(bundle: dict) -> dict:
    primary, review = bundle["primary"], bundle["review"]
    decisions = bundle["reconcile"]
    outcome = bundle["outcome"]
    year = str((bundle.get("input") or {}).get("year") or "")
    korean = _korean_place(bundle)
    identity_ok = decisions["identity"]["decision"] == "agree" and all(
        r["identity"].get("value") == "same_paper" for r in (primary, review))
    fields, conflicts = {}, {}
    for name in FIELDS:
        claims = [r["fields"].get(name, {}) for r in (primary, review)]
        decision = decisions["fields"].get(name, {"decision": "neither", "reason": ""})
        values = [str(c.get("value") or "").strip() for c in claims]
        if outcome == "not_found":
            rule = "outcome_not_found"
        elif not all(values):
            rule = "one_sided" if any(values) else "unconfirmed"
        elif decision["decision"] != "agree":
            rule = "reconcile_" + decision["decision"]
        elif name in PAPER_FIELDS and not identity_ok:
            rule = "identity_not_agreed"
        else:
            rule = "reconcile_agree"
        if outcome != "not_found":
            rule = _undecided_country(name, claims, korean) or rule
        if name in CLASSIFICATION_FIELDS and rule == "reconcile_agree":
            rule = independent_problem(name, [primary, review], year, korean_place_as_korea=korean)
            if not rule and not all(_direct_captured_evidence(bundle, name, claim) for claim in claims):
                rule = "capture_unverified"
            rule = rule or "reconcile_agree"
        adopted = rule == "reconcile_agree"
        evidence: dict = {}
        if adopted:
            first = next((c["evidence"][0] for c in claims if c.get("evidence")), {})
            if name in CLASSIFICATION_FIELDS:
                first = _direct_captured_evidence(bundle, name, claims[0])[0]
            evidence = dict(first)
        elif rule == "reconcile_disagree":
            conflicts[name] = [{"value": c.get("value", ""), "source": (c.get("evidence") or [{}])[0]}
                               for c in claims]
        fields[name] = {**_field_entry(bundle, name, claims, adopted, rule, evidence),
                        "reconcile": dict(decision)}
    accepted = {name: entry["value"] for name, entry in fields.items() if entry["adopted"]}
    core = bool(identity_ok and accepted.get("presentation_date")
                and (accepted.get("title_ko") or accepted.get("title_en")))
    return {"policy": RECONCILE_POLICY, "identity_agreed": identity_ok, "core_verified": core,
            "accepted": accepted, "fields": fields, "conflicts": conflicts,
            "reconcile_identity": dict(decisions["identity"])}


def _string_agreement(bundle: dict) -> dict:
    primary, review = bundle["primary"], bundle["review"]
    outcome = bundle["outcome"]
    year_hint = str((bundle.get("input") or {}).get("year") or "")
    korean = _korean_place(bundle)
    identity_ok = all(_confirmed(r["identity"], "paper_identity") and r["identity"]["value"] == "same_paper"
                      for r in (primary, review)) and _capture_verified(bundle, [primary["identity"],
                                                                                  review["identity"]])
    event_ok = all(_confirmed(r["event"], "event_year") for r in (primary, review)) and _capture_verified(
        bundle, [primary["event"], review["event"]])
    year = primary["event"]["value"]
    same_year = bool(re.fullmatch(r"\d{4}", year) and year == review["event"]["value"] and year == year_hint)
    core = identity_ok and event_ok and same_year
    comparison_year = year if same_year and event_ok else ""
    fields, conflicts = {}, {}
    for name in FIELDS:
        claims = [r["fields"].get(name, {}) for r in (primary, review)]
        scope = "individual_presentation" if name == "presentation_date" else ""
        confirmed = all(_confirmed(c, scope) for c in claims)
        equal, rule = _agreement(name, claims[0]["value"], claims[1]["value"], comparison_year) \
            if confirmed else (False, "unconfirmed")
        if confirmed and name == "host_country" and coded_countries(claims):
            # 원문 표기가 달라도 같은 나라일 수 있다. 코드 형식·일치는 아래 independent_problem이 본다.
            equal, rule = True, "country_code"
        if confirmed and equal and not _capture_verified(bundle, claims):
            equal, rule = False, "capture_unverified"
        undecided = _undecided_country(name, claims, korean)
        if undecided:
            equal, rule = False, undecided
        if name in CLASSIFICATION_FIELDS and confirmed and equal:
            problem = independent_problem(name, [primary, review], comparison_year, korean_place_as_korea=korean)
            if problem or not all(_direct_captured_evidence(bundle, name, claim) for claim in claims):
                equal, rule = False, problem or "capture_unverified"
        adopted = confirmed and equal
        evidence: dict = {}
        if adopted:
            first = (_direct_captured_evidence(bundle, name, claims[0])[0]
                     if name in CLASSIFICATION_FIELDS else claims[0]["evidence"][0])
            evidence = dict(first)
        elif any(c.get("status") == "conflict" for c in claims) or (confirmed and rule == "values_differ"):
            conflicts[name] = [{"value": c.get("value", ""), "source": (c.get("evidence") or [{}])[0]}
                               for c in claims]
        fields[name] = _field_entry(bundle, name, claims, adopted, rule, evidence)
    accepted = {name: entry["value"] for name, entry in fields.items() if entry["adopted"]}
    day = accepted.get("presentation_date", "")
    core = bool(core and day and day[:4] == year and (accepted.get("title_ko") or accepted.get("title_en")))
    if day and (accepted.get("event_start", day) > day or accepted.get("event_end", day) < day):
        core = False
        conflicts["presentation_date"] = [{"value": day, "source": fields["presentation_date"]["evidence"]}]
    if not core or outcome != "found":
        # 핵심이 확인되지 않으면 이 정책은 아무것도 채택하지 않는다.
        for entry in fields.values():
            if entry["adopted"]:
                entry.update(adopted=False, value="", evidence={}, rule="core_not_verified")
                entry.pop(COUNTRY_CODE_KEY, None)
        accepted = {}
    return {"policy": STRING_POLICY, "identity_agreed": identity_ok, "core_verified": bool(core and outcome == "found"),
            "accepted": accepted, "fields": fields, "conflicts": conflicts,
            "event_year_agreed": bool(event_ok and same_year)}


def judge(bundle: dict) -> dict:
    """bundle을 확인하고 정책에 따라 판정한다. 결속이 깨졌으면 아무것도 채택하지 않는 판정을 돌려준다."""
    try:
        check_bundle(bundle)
        return _reconciled(bundle) if bundle.get("reconcile") is not None else _string_agreement(bundle)
    except (BundleError, KeyError, TypeError, IndexError) as exc:
        return {"policy": "invalid", "identity_agreed": False, "core_verified": False, "accepted": {},
                "fields": {}, "conflicts": {}, "error": str(exc) or type(exc).__name__}


def verify_result(result: dict) -> bool:
    """저장된 결과의 판정이 같은 bundle로 다시 계산한 판정과 같은가."""
    try:
        bundle = result["proof"]["bundle"]
        return judge(bundle) == result["proof"]["verdict"] and result["proof"]["digest"] == fingerprint(
            {"bundle": bundle, "verdict": result["proof"]["verdict"]})
    except (KeyError, TypeError):
        return False
