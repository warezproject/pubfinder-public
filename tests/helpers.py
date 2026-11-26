"""시험용 합성 자료. 지어낸 학술대회·논문·사람 이름만 쓴다(실제 행사·논문·사람과 무관하다)."""

from __future__ import annotations

import copy
import json

from pubfinder.prompts import PRIMARY_SCHEMA, REVIEW_SCHEMA
from pubfinder.query import Query, make_query
from pubfinder.quotes import capture_summary
from pubfinder.reconcile import RECONCILE_SCHEMA
from pubfinder.verdict import make_bundle, report

TITLE = "A Made-up Conference Paper for Offline Tests"
VENUE = "IMAGCONF 2026"
AUTHORS = ["Alex Sample", "Riley Fixture"]
PROGRAM_URL = "https://imagconf.example.org/2026/program"
EVENT_URL = "https://imagconf.example.org/2026/"
PROGRAM_ITEM = f"Paper 17: {TITLE}, Alex Sample, Riley Fixture"
PAGES = {
    PROGRAM_URL: ("IMAGCONF 2026 Technical Program. Session B2: Probing Methods, Wednesday 10 June 2026, 14:00-15:30. "
                  f"{PROGRAM_ITEM}."),
    EVENT_URL: ("The 9th International Conference on Imaginary Results (IMAGCONF 2026) will be held in Busan, "
                "Republic of Korea, June 9-11, 2026. Organized by the Imaginary Results Society. "
                "IMAGCONF is an international conference bringing together researchers from all regions."),
}


def ev(url: str, quote: str, scope: str, locator: str = "page") -> dict:
    return {"url": url, "quote": quote, "locator": locator, "observation": "read on page",
            "tool": "aside_native_browser", "scope": scope}


def claim(value: str, scope: str, evidence: list[dict], **extra) -> dict:
    return {"status": "confirmed", "value": value, "scope": scope, "reason": "", "evidence": evidence, **extra}


def unknown(reason: str = "not found", **extra) -> dict:
    return {"status": "unknown", "value": "", "scope": "", "reason": reason, "evidence": [], **extra}


def observations(*, session: str = "B2: Probing Methods", with_time: bool = True) -> dict:
    program = PROGRAM_ITEM
    fields = {
        "title_en": claim(TITLE, "paper_metadata", [ev(PROGRAM_URL, program, "official_program")]),
        "title_ko": unknown(),
        "observed_authors": claim("Alex Sample, Riley Fixture", "paper_metadata",
                                  [ev(PROGRAM_URL, program, "official_program")]),
        "session": claim(session, "paper_metadata", [ev(PROGRAM_URL, "Session B2: Probing Methods", "official_program")]),
        "presentation_time": (claim("14:00-15:30", "paper_metadata",
                                    [ev(PROGRAM_URL, "Wednesday 10 June 2026, 14:00-15:30", "official_program")])
                              if with_time else unknown()),
        "venue_name": claim("The 9th International Conference on Imaginary Results (IMAGCONF 2026)", "event",
                            [ev(EVENT_URL, "The 9th International Conference on Imaginary Results (IMAGCONF 2026)",
                                "official_event")]),
        "presentation_date": claim("2026-06-10", "individual_presentation",
                                   [ev(PROGRAM_URL, "Session B2: Probing Methods, Wednesday 10 June 2026",
                                       "official_program")]),
        "event_start": claim("2026-06-09", "event", [ev(EVENT_URL, "Republic of Korea, June 9-11, 2026", "official_event")]),
        "event_end": claim("2026-06-11", "event", [ev(EVENT_URL, "Republic of Korea, June 9-11, 2026", "official_event")]),
        "organizer": claim("Imaginary Results Society", "event",
                           [ev(EVENT_URL, "Organized by the Imaginary Results Society", "official_event")]),
        "location": claim("Busan, Republic of Korea", "event",
                          [ev(EVENT_URL, "will be held in Busan, Republic of Korea", "official_event")]),
        "host_country": claim("Republic of Korea", "single_host_country",
                              [ev(EVENT_URL, "will be held in Busan, Republic of Korea", "official_event")],
                              country_code="KR"),
        "conference_scope": claim("international", "classification_stated",
                                  [ev(EVENT_URL, "IMAGCONF is an international conference bringing together researchers",
                                      "official_event")]),
    }
    return {
        "identity": claim("same_paper", "paper_identity", [ev(PROGRAM_URL, program, "official_program")]),
        "event": claim("2026", "event_year", [ev(EVENT_URL, "(IMAGCONF 2026) will be held in Busan", "official_event")]),
        "fields": fields,
        "limitations": [],
    }


def page_capture(url: str, text: str | None = None) -> dict:
    body = PAGES[url] if text is None else text
    return {"status": "fetched", "kind": "page", "visible_text": body, "text": body, "final_url": url,
            "content_type": "text/html", "sha256": "0" * 64, "collected_at": 0}


def agree_all(*, disagree: tuple[str, ...] = (), identity: str = "agree") -> dict:
    from pubfinder.fields import FIELDS
    return {"schema_version": RECONCILE_SCHEMA, "identity": {"decision": identity, "reason": "same title and authors"},
            "fields": {name: {"decision": "disagree" if name in disagree else "agree", "reason": ""}
                       for name in FIELDS}}


def make_test_query(year: str = "2026", authors: list[str] | None = None, venue: str = VENUE,
                    title: str = TITLE) -> Query:
    return make_query(title, venue, authors or AUTHORS, year)


def bundle(query: Query, primary_obs: dict, review_obs: dict, *, captures: dict | None = None,
           reconcile: dict | None = None, outcome: str = "found", korean: bool = True) -> dict:
    primary = report("primary", copy.deepcopy(primary_obs), run_id="run-1", session_id="session-a")
    review = report("review", copy.deepcopy(review_obs), run_id="run-1", session_id="session-b",
                    review_run_id="review-1")
    captured = captures if captures is not None else {url: page_capture(url) for url in PAGES}
    summary = capture_summary([primary, review], captured)
    from pubfinder.reconcile import parse_reconcile
    return make_bundle(query.source_input(), outcome=outcome, review_run_id="review-1", primary=primary,
                       review=review, captures=summary,
                       reconcile=parse_reconcile(reconcile) if reconcile is not None else None,
                       korean_place_as_korea=korean, excluded_domains=("arxiv.org",))


def primary_file(query: Query, obs: dict, *, run_id: str = "run-1", outcome: str = "found", **extra) -> bytes:
    envelope = {"schema_version": PRIMARY_SCHEMA, "run_id": run_id, "input_fingerprint": query.fingerprint(),
                "outcome": outcome, "reason": "", "limitations": obs.get("limitations", []),
                "trail": [{"query": "made-up query", "url": PROGRAM_URL, "purpose": "program", "result": "confirmed",
                           "controls": "", "observation": "program page"}],
                "identity": obs["identity"], "event": obs["event"], "fields": obs["fields"], **extra}
    return json.dumps(envelope, ensure_ascii=False).encode("utf-8")


def review_file(obs: dict, *, run_id: str = "run-1", review_run_id: str = "review-1", **extra) -> bytes:
    envelope = {"schema_version": REVIEW_SCHEMA, "run_id": run_id, "review_run_id": review_run_id,
                "limitations": obs.get("limitations", []), "identity": obs["identity"], "event": obs["event"],
                "fields": obs["fields"], **extra}
    return json.dumps(envelope, ensure_ascii=False).encode("utf-8")
