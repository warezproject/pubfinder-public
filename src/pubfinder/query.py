"""사용자 입력(논문명·학술대회명·저자명·연도)을 조사 입력으로 바꾼다."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .textutil import fingerprint, has_hangul

_YEAR = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)")
MAX_TITLE = 2000
MAX_VENUE = 2000
MAX_AUTHOR = 300
MAX_AUTHORS = 100


class QueryError(ValueError):
    """입력이 조사에 쓸 수 없는 꼴이다. 메시지는 사용자에게 그대로 보인다."""


@dataclass(frozen=True)
class Query:
    title: str
    venue: str
    authors: tuple[str, ...]
    year: str = ""
    # input: 연도 입력에서 읽음, venue_name: 학술대회명 속 연도, "": 연도 없음
    year_source: str = ""
    year_input: str = ""
    extra: dict = field(default_factory=dict, compare=False, hash=False)

    def source_input(self) -> dict:
        """지시문·대조·판정이 함께 보는 입력 묶음. 지문은 이 묶음으로 만든다."""
        korean = has_hangul(self.title)
        return {
            "schema_version": 1,
            "title_ko": self.title if korean else "",
            "title_en": "" if korean else self.title,
            "venue": self.venue,
            "year": self.year,
            "authors": list(self.authors),
        }

    def fingerprint(self) -> str:
        return fingerprint(self.source_input())


def years_in(text: str) -> list[str]:
    """글 속 4자리 연도(1900~2099)를 나온 순서대로, 겹치지 않게."""
    return list(dict.fromkeys(_YEAR.findall(text or "")))


def resolve_year(raw: str, venue: str) -> tuple[str, str]:
    """(연도, 출처). 연도 입력이 있으면 그 안의 4자리 연도 하나를 쓴다.

    `2026`, `2026-06-10`, `06/10/2026`, `June 2026`, `2026년 6월`을 모두 2026으로 읽는다.
    입력이 없으면 학술대회명 속 4자리 연도가 하나일 때만 그 연도를 쓴다.
    """
    raw = (raw or "").strip()
    if raw:
        found = years_in(raw)
        if not found:
            raise QueryError(f"연도 입력에서 4자리 연도를 읽지 못했습니다: {raw!r}")
        if len(found) > 1:
            raise QueryError(f"연도 입력에 연도가 둘 이상 있습니다: {raw!r}")
        return found[0], "input"
    found = years_in(venue)
    if len(found) == 1:
        return found[0], "venue_name"
    return "", ""


def _clean(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def make_query(title: str, venue: str, authors: list[str] | tuple[str, ...], year: str = "") -> Query:
    title, venue = _clean(title), _clean(venue)
    names = [_clean(name) for name in authors or []]
    names = [name for name in names if name]
    if not title:
        raise QueryError("논문명이 필요합니다")
    if not venue:
        raise QueryError("출처명(학술대회명)이 필요합니다")
    if not names:
        raise QueryError("저자명이 한 명 이상 필요합니다")
    if len(title) > MAX_TITLE or len(venue) > MAX_VENUE:
        raise QueryError("논문명·학술대회명은 2000자 이내여야 합니다")
    if len(names) > MAX_AUTHORS or any(len(name) > MAX_AUTHOR for name in names):
        raise QueryError("저자는 100명, 이름은 300자 이내여야 합니다")
    resolved, source = resolve_year(year, venue)
    return Query(title=title, venue=venue, authors=tuple(names), year=resolved, year_source=source,
                 year_input=(year or "").strip())
