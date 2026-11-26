"""조사 항목 13개(행사 7, 논문 6)와 묶음."""

from __future__ import annotations

# 지시문·대조·판정이 같은 순서를 쓴다.
FIELDS: tuple[str, ...] = (
    "venue_name",
    "presentation_date",
    "event_start",
    "event_end",
    "organizer",
    "location",
    "host_country",
    "conference_scope",
    "title_ko",
    "title_en",
    "observed_authors",
    "session",
    "presentation_time",
)

EVENT_FIELDS: tuple[str, ...] = (
    "venue_name", "event_start", "event_end", "organizer", "location", "host_country", "conference_scope",
)

# 같은 논문이라는 합의가 있어야 채택하는 항목이다.
PAPER_FIELDS: frozenset[str] = frozenset(
    {"presentation_date", "title_ko", "title_en", "observed_authors", "session", "presentation_time"}
)

# 행사연도가 입력 연도와 같고 직접 인용이 다시 연 원문에서 확인돼야 채택하는 항목이다.
CLASSIFICATION_FIELDS: frozenset[str] = frozenset({"host_country", "conference_scope"})

DATE_FIELDS: frozenset[str] = frozenset({"presentation_date", "event_start", "event_end"})

TITLE_FIELDS: tuple[str, ...] = ("title_ko", "title_en")

COUNTRY_CODE_KEY = "country_code"

# 근거의 도구 값. 조사 세션이 Aside 브라우저로 직접 연 원문이라는 표시다.
EVIDENCE_TOOL = "aside_native_browser"

LABELS = {
    "venue_name": "행사명",
    "presentation_date": "발표일",
    "event_start": "행사 시작일",
    "event_end": "행사 종료일",
    "organizer": "주관",
    "location": "장소",
    "host_country": "개최국",
    "conference_scope": "국내·국제 구분",
    "title_ko": "한글 제목",
    "title_en": "영문 제목",
    "observed_authors": "저자(원문 순서)",
    "session": "세션",
    "presentation_time": "발표 시각",
}


def group(name: str) -> str:
    return "event" if name in EVENT_FIELDS else "paper"
