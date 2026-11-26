"""창이 부르는 동작. tkinter 없이 시험할 수 있게 화면과 나눠 둔다.

입력이 틀렸거나 모델·추론 강도가 비었으면 대기열에 넣기 전에 오류(`QueryError`, `SettingsError`,
`BatchError`)를 낸다. 창은 그 문구를 그대로 보여 준다.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Callable

from .. import __version__
from ..query import make_query
from .jobs import Job, JobManager
from .storage import SAMPLE_CSV, Store, export_csv, parse_batch_csv

MAX_EXPORT = 1000


class BatchError(ValueError):
    """CSV에 조사할 줄이 없다. `errors`에 줄마다 까닭이 있다."""

    def __init__(self, message: str, errors: list[str]) -> None:
        super().__init__(message)
        self.errors = errors


def _default_opener(path: Path) -> None:
    if hasattr(os, "startfile"):
        os.startfile(str(path))  # noqa: S606 - 사용자가 누른 버튼으로 결과 폴더를 연다.


def split_authors(text: str | list[str]) -> list[str]:
    """한 줄에 한 명, 또는 세미콜론으로 나눈 저자 이름."""
    parts = text if isinstance(text, list) else re.split(r"[\n;；]", text or "")
    return [str(name).strip() for name in parts if str(name).strip()]


class Controller:
    def __init__(self, store: Store, jobs: JobManager, *, opener: Callable[[Path], None] = _default_opener) -> None:
        self.store = store
        self.jobs = jobs
        self.opener = opener

    # 상태·설정 -------------------------------------------------------------------------------------------------

    def state(self) -> dict:
        return {"version": __version__, "data_dir": str(self.store.data_dir), "runs_dir": str(self.store.runs_dir),
                "settings": self.store.settings_view(), "busy": self.jobs.busy()}

    def save_settings(self, values: dict) -> dict:
        self.store.save_settings(values)
        return self.store.settings_view()

    # 작업 -----------------------------------------------------------------------------------------------------

    def submit_search(self, title: str, venue: str, authors: str | list[str], year: str = "") -> Job:
        names = split_authors(authors)
        make_query(str(title or ""), str(venue or ""), names, str(year or ""))
        self.store.settings().check()  # 모델·추론 강도가 비었으면 대기열에 넣지 않고 바로 알린다.
        row = {"title": str(title).strip(), "venue": str(venue).strip(), "authors": names,
               "year": str(year or "").strip()}
        return self.jobs.submit_queries([row], "search")

    def submit_batch(self, csv_text: str) -> tuple[Job, list[str]]:
        rows, errors = parse_batch_csv(csv_text)
        if not rows:
            raise BatchError("조사할 줄이 없습니다", errors)
        self.store.settings().check()
        return self.jobs.submit_queries(rows, "batch"), errors

    def submit_check(self, *, with_exec: bool) -> Job:
        return self.jobs.submit_check(with_exec=with_exec)

    def job(self, job_id: str) -> dict | None:
        job = self.jobs.get(job_id)
        return job.view() if job else None

    def cancel(self, job_id: str) -> bool:
        return self.jobs.cancel(job_id)

    def busy(self) -> bool:
        return self.jobs.busy()

    def shutdown(self, timeout: float = 45.0) -> None:
        """대기열을 멈추고, 진행 중이던 조사가 자기 Aside 세션을 멈추고 끝날 때까지 기다린다(세션 중지 제한 30초 + 여유)."""
        self.jobs.stop()
        self.jobs.join(timeout)

    # 결과 -----------------------------------------------------------------------------------------------------

    def results(self) -> list[dict]:
        return self.store.list_results()

    def result(self, run_id: str) -> dict:
        return self.store.load_result(run_id)

    def export(self, run_ids: list[str], path: str | Path) -> int:
        """고른 결과를 엑셀용 CSV(UTF-8 BOM)로 쓴다. 쓴 줄 수."""
        results = [(run_id, self.store.load_result(run_id)) for run_id in list(run_ids)[:MAX_EXPORT]]
        Path(path).write_text(export_csv(results), encoding="utf-8", newline="")
        return len(results)

    def save_sample_csv(self, path: str | Path) -> None:
        Path(path).write_text(SAMPLE_CSV, encoding="utf-8", newline="")

    def open_folder(self, run_id: str = "") -> Path:
        """결과 폴더(실행 ID를 주면 그 실행 폴더)를 연다. 실행 ID가 틀리면 ValueError."""
        target = self.store.run_folder(run_id) if run_id else self.store.runs_dir
        target.mkdir(parents=True, exist_ok=True)
        self.opener(target)
        return target
