"""창용 저장소: 데이터 폴더, 설정 파일, 지난 결과 목록, CSV 읽기·쓰기."""

from __future__ import annotations

import csv
import io
import json
import os
import re
import sys
import tomllib
from dataclasses import fields as dataclass_fields
from pathlib import Path
from typing import Any

from ..aside.settings import AsideSettings
from ..config import load
from ..fields import COUNTRY_CODE_KEY
from ..query import QueryError, make_query
from ..runner import RunOptions

CONFIG_NAME = "pubfinder.local.toml"
RUNS_DIR = "pubfinder-runs"
RUN_ID = re.compile(r"^\d{8}T\d{6}-[0-9a-f]{8}$")
MAX_BATCH_ROWS = 200
# 화면에서 바꿀 수 있는 설정 키.
EDITABLE_ASIDE = {"model": str, "effort": str, "fallback_model": str, "executable": str, "research_timeout": float}
# 비우면 설정 파일에서 키를 지우는 칸. 대체 모델은 비워 저장하면 "바꾸지 않음"이라 키를 남긴다.
OPTIONAL_ASIDE = {"executable"}
# 화면의 모델 기본값(2026-11-25 사용자 결정). 설정 파일에 키가 없을 때만 쓴다.
# 주 모델은 Aside에 Claude 계정으로 연결한 모델, 대체 모델은 Anthropic API 모델이다. 명령줄에는 기본값이 없다.
DEFAULT_MODEL = "claude-code/claude-sonnet-5-5"
DEFAULT_FALLBACK_MODEL = "anthropic/claude-sonnet-5-5"
EDITABLE_RESEARCH = {"korean_place_as_korea": bool}


def _writable(folder: Path) -> bool:
    try:
        folder.mkdir(parents=True, exist_ok=True)
        probe = folder / ".pubfinder-write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def default_data_dir() -> Path:
    """exe면 exe가 있는 폴더, 소스로 돌리면 현재 폴더. 쓸 수 없으면 %LOCALAPPDATA%\\pubfinder."""
    base = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path.cwd()
    if _writable(base):
        return base
    fallback = Path(os.getenv("LOCALAPPDATA") or Path.home()) / "pubfinder"
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback


def _toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(float(value)) if isinstance(value, float) else str(value)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    return json.dumps(str(value), ensure_ascii=False)


class Store:
    def __init__(self, data_dir: Path, *, settings_overrides: dict | None = None) -> None:
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.config_path = self.data_dir / CONFIG_NAME
        self.runs_dir = self.data_dir / RUNS_DIR
        # 시험에서 가짜 Aside를 쓰려고 덮어쓰는 값. 화면 설정보다 먼저다.
        self.settings_overrides = dict(settings_overrides or {})

    # 설정 -----------------------------------------------------------------------------------------------------

    def load_config(self) -> tuple[dict, dict]:
        return load(self.config_path) if self.config_path.is_file() else ({}, {})

    def settings(self) -> AsideSettings:
        """저장된 설정으로 만든 Aside 설정. 추론 강도가 비어 있을 수 있어 check()는 부르는 쪽이 한다."""
        aside, _ = self.load_config()
        known = {item.name for item in dataclass_fields(AsideSettings)}
        values = {"model": DEFAULT_MODEL, "effort": "", "fallback_model": DEFAULT_FALLBACK_MODEL,
                  **{k: v for k, v in aside.items() if k in known}}
        values.update(self.settings_overrides)
        return AsideSettings(**values)

    def options(self) -> RunOptions:
        _, research = self.load_config()
        defaults = RunOptions()
        return RunOptions(
            out_dir=self.runs_dir,
            korean_place_as_korea=bool(research.get("korean_place_as_korea", defaults.korean_place_as_korea)),
            excluded_domains=tuple(research.get("excluded_domains", defaults.excluded_domains)),
            run_deadline=float(research.get("run_deadline", defaults.run_deadline)),
            keep_captures=bool(research.get("keep_captures", defaults.keep_captures)),
        )

    def settings_view(self) -> dict:
        aside, research = self.load_config()
        settings = self.settings()
        return {
            "model": settings.model,
            "effort": settings.effort,
            "fallback_model": settings.fallback_model,
            "executable": aside.get("executable", ""),
            "default_executable": str(AsideSettings(model="", effort="").executable),
            "research_timeout": float(aside.get("research_timeout", settings.research_timeout)),
            "korean_place_as_korea": bool(research.get("korean_place_as_korea", True)),
            "configured": bool(settings.model and settings.effort),
        }

    def save_settings(self, values: dict) -> None:
        """화면에서 바꾼 값만 고치고 설정 파일의 다른 키는 그대로 둔다."""
        raw: dict[str, dict] = {"aside": {}, "research": {}}
        if self.config_path.is_file():
            raw = tomllib.loads(self.config_path.read_text(encoding="utf-8"))
            raw.setdefault("aside", {})
            raw.setdefault("research", {})
        for key, kind in EDITABLE_ASIDE.items():
            if key not in values:
                continue
            value = values[key]
            if kind is str:
                value = str(value or "").strip()
                if key in OPTIONAL_ASIDE and not value:
                    raw["aside"].pop(key, None)
                    continue
            else:
                value = float(value)
                if value <= 0:
                    raise ValueError(f"{key}는 0보다 커야 합니다")
            raw["aside"][key] = value
        for key in EDITABLE_RESEARCH:
            if key in values:
                raw["research"][key] = bool(values[key])
        lines = ["# pubfinder 설정. 창의 설정 탭에서 고칠 수 있다.", "[aside]"]
        lines += [f"{key} = {_toml_value(value)}" for key, value in raw["aside"].items()]
        lines += ["", "[research]"]
        lines += [f"{key} = {_toml_value(value)}" for key, value in raw["research"].items()]
        text = "\n".join(lines) + "\n"
        tomllib.loads(text)  # 쓰기 전에 다시 읽혀야 한다.
        temporary = self.config_path.with_suffix(".tmp")
        temporary.write_text(text, encoding="utf-8")
        os.replace(temporary, self.config_path)

    # 결과 -----------------------------------------------------------------------------------------------------

    def run_folder(self, run_id: str) -> Path:
        if not RUN_ID.fullmatch(run_id or ""):
            raise ValueError("실행 ID가 올바르지 않습니다")
        return self.runs_dir / run_id

    def load_result(self, run_id: str) -> dict:
        return json.loads((self.run_folder(run_id) / "result.json").read_text(encoding="utf-8"))

    def list_results(self, limit: int = 300) -> list[dict]:
        if not self.runs_dir.is_dir():
            return []
        rows = []
        for folder in sorted(self.runs_dir.iterdir(), reverse=True):
            if len(rows) >= limit:
                break
            if not RUN_ID.fullmatch(folder.name) or not (folder / "result.json").is_file():
                continue
            try:
                rows.append(summary(json.loads((folder / "result.json").read_text(encoding="utf-8")), folder.name))
            except (OSError, ValueError):
                continue
        return rows


def _adopted(result: dict, name: str) -> str:
    item = (result.get("fields") or {}).get(name) or {}
    return str(item.get("value") or "") if item.get("status") == "adopted" else ""


def summary(result: dict, run_id: str) -> dict:
    query = result.get("query") or {}
    country = (result.get("fields") or {}).get("host_country") or {}
    return {
        "run_id": run_id,
        "status": result.get("status", ""),
        "status_text": result.get("status_text", ""),
        "title": query.get("title", ""),
        "venue": query.get("venue", ""),
        "year": query.get("year", ""),
        "adopted_count": (result.get("summary") or {}).get("adopted_count", 0),
        "presentation_date": _adopted(result, "presentation_date"),
        "venue_name": _adopted(result, "venue_name"),
        "host_country": country.get(COUNTRY_CODE_KEY, "") if country.get("status") == "adopted" else "",
        "reason": result.get("reason", ""),
    }


EXPORT_COLUMNS = [
    ("행사명", "venue_name"), ("발표일", "presentation_date"), ("행사 시작일", "event_start"),
    ("행사 종료일", "event_end"), ("장소", "location"), ("개최국", "host_country"),
    ("국내·국제 구분", "conference_scope"), ("세션", "session"), ("발표 시각", "presentation_time"),
    ("영문 제목", "title_en"), ("한글 제목", "title_ko"), ("저자(원문 순서)", "observed_authors"), ("주관", "organizer"),
]


def export_csv(results: list[tuple[str, dict]]) -> str:
    """엑셀에서 한글이 깨지지 않게 BOM을 붙인 CSV. 채택된 값만 칸에 넣는다."""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["실행 ID", "상태", "입력 논문명", "입력 학술대회명", "입력 저자", "입력 연도",
                     *[label for label, _ in EXPORT_COLUMNS], "국가 코드", "채택 수", "주의", "실패 사유"])
    for run_id, result in results:
        query = result.get("query") or {}
        country = (result.get("fields") or {}).get("host_country") or {}
        writer.writerow([
            run_id, result.get("status_text") or result.get("status", ""), query.get("title", ""),
            query.get("venue", ""), "; ".join(query.get("authors") or []), query.get("year", ""),
            *[_adopted(result, name) for _, name in EXPORT_COLUMNS],
            country.get(COUNTRY_CODE_KEY, "") if country.get("status") == "adopted" else "",
            (result.get("summary") or {}).get("adopted_count", ""), " / ".join(result.get("warnings") or []),
            result.get("reason", ""),
        ])
    return "﻿" + buffer.getvalue()


_HEADERS = {
    "title": {"title", "논문명", "제목", "논문 제목"},
    "venue": {"venue", "학술대회명", "학술대회", "출처명", "행사명"},
    "authors": {"authors", "author", "저자", "저자명"},
    "year": {"year", "연도", "날짜", "발표연도"},
}
SAMPLE_CSV = ("﻿title,venue,authors,year\n"
              "첫 번째 논문 제목,학술대회명 2026,저자1; 저자2,2026\n"
              "두 번째 논문 제목,다른 학술대회 2026,저자3,2026\n")


def parse_batch_csv(text: str) -> tuple[list[dict], list[str]]:
    """CSV 글을 조사 입력 목록으로. 칸 이름은 영어(title, venue, authors, year)나 한국어(논문명, 학술대회명, 저자, 연도)."""
    text = (text or "").lstrip("﻿")
    reader = csv.reader(io.StringIO(text))
    try:
        header = next(reader)
    except StopIteration:
        return [], ["CSV가 비어 있습니다"]
    columns: dict[str, int] = {}
    for index, name in enumerate(header):
        key = name.strip().lower()
        for field, aliases in _HEADERS.items():
            if key in aliases and field not in columns:
                columns[field] = index
    missing = [label for label, field in (("논문명(title)", "title"), ("학술대회명(venue)", "venue"),
                                           ("저자(authors)", "authors")) if field not in columns]
    if missing:
        return [], ["첫 줄에 칸 이름이 없습니다: " + ", ".join(missing)]
    rows, errors = [], []
    for line_no, row in enumerate(reader, start=2):
        if not any(cell.strip() for cell in row):
            continue
        if len(rows) >= MAX_BATCH_ROWS:
            errors.append(f"{MAX_BATCH_ROWS}건까지만 받습니다. {line_no}번째 줄부터 뺐습니다")
            break

        def cell(field: str, row: list[str] = row) -> str:
            index = columns.get(field)
            return row[index].strip() if index is not None and index < len(row) else ""

        authors = [name.strip() for name in re.split(r"[;；]", cell("authors")) if name.strip()]
        values = {"title": cell("title"), "venue": cell("venue"), "authors": authors, "year": cell("year")}
        try:
            make_query(values["title"], values["venue"], authors, values["year"])
        except QueryError as exc:
            errors.append(f"{line_no}번째 줄: {exc}")
            continue
        rows.append(values)
    return rows, errors
