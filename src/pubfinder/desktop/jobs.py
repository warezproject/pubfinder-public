"""조사 작업 대기열. 작업은 한 번에 하나씩 차례로 돌고, 취소하면 이 프로그램이 띄운 Aside 세션을 멈춘다."""

from __future__ import annotations

import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field, replace
from typing import Any, Callable

from ..aside.settings import SettingsError
from ..check import check_aside
from ..query import QueryError, make_query
from ..runner import Run
from .storage import Store, summary

MAX_KEPT_JOBS = 50


@dataclass
class Item:
    index: int
    title: str
    venue: str
    authors: list[str]
    year: str
    state: str = "queued"  # queued, running, done, failed, cancelled
    stage: str = ""
    run_id: str = ""
    result: dict = field(default_factory=dict)
    error: str = ""

    def view(self) -> dict:
        return {"index": self.index, "title": self.title, "venue": self.venue, "authors": self.authors,
                "year": self.year, "state": self.state, "stage": self.stage, "run_id": self.run_id,
                "error": self.error, "summary": self.result}


@dataclass
class Job:
    id: str
    kind: str  # search, batch, check
    created: float
    items: list[Item] = field(default_factory=list)
    state: str = "queued"
    with_exec: bool = False
    report: dict = field(default_factory=dict)
    error: str = ""
    cancel: threading.Event = field(default_factory=threading.Event)

    def view(self) -> dict:
        done = sum(item.state in {"done", "failed", "cancelled"} for item in self.items)
        return {"id": self.id, "kind": self.kind, "created": self.created, "state": self.state,
                "total": len(self.items), "done": done, "items": [item.view() for item in self.items],
                "report": self.report, "error": self.error}


class JobManager:
    def __init__(self, store: Store, *, resolve: Callable[..., Any] | None = None) -> None:
        self.store = store
        self.resolve = resolve
        self._jobs: dict[str, Job] = {}
        self._order: deque[str] = deque()
        self._queue: deque[str] = deque()
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stopping = threading.Event()
        self._worker = threading.Thread(target=self._loop, name="pubfinder-jobs", daemon=True)
        self._worker.start()

    # 등록 -----------------------------------------------------------------------------------------------------

    def _add(self, job: Job) -> Job:
        with self._lock:
            self._jobs[job.id] = job
            self._order.append(job.id)
            self._queue.append(job.id)
            while len(self._order) > MAX_KEPT_JOBS:
                old = self._order.popleft()
                if old not in self._queue and self._jobs.get(old) and self._jobs[old].state != "running":
                    self._jobs.pop(old, None)
        self._wake.set()
        return job

    def submit_queries(self, rows: list[dict], kind: str) -> Job:
        job = Job(id=uuid.uuid4().hex[:12], kind=kind, created=time.time())
        job.items = [Item(index=i, title=row["title"], venue=row["venue"], authors=list(row["authors"]),
                          year=row.get("year", "")) for i, row in enumerate(rows)]
        return self._add(job)

    def submit_check(self, *, with_exec: bool) -> Job:
        return self._add(Job(id=uuid.uuid4().hex[:12], kind="check", created=time.time(), with_exec=with_exec))

    # 조회·취소 -------------------------------------------------------------------------------------------------

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def recent(self, limit: int = 10) -> list[dict]:
        with self._lock:
            ids = list(self._order)[-limit:]
            return [self._jobs[i].view() for i in reversed(ids) if i in self._jobs]

    def cancel(self, job_id: str) -> bool:
        job = self.get(job_id)
        if job is None:
            return False
        job.cancel.set()
        with self._lock:
            if job_id in self._queue:
                self._queue.remove(job_id)
                job.state = "cancelled"
                for item in job.items:
                    item.state = "cancelled"
        return True

    def cancel_all(self) -> None:
        with self._lock:
            ids = list(self._jobs)
        for job_id in ids:
            self.cancel(job_id)

    def busy(self) -> bool:
        with self._lock:
            return bool(self._queue) or any(job.state == "running" for job in self._jobs.values())

    def stop(self) -> None:
        self._stopping.set()
        self.cancel_all()
        self._wake.set()

    def join(self, timeout: float) -> None:
        """진행 중이던 조사가 Aside 세션을 멈추고 끝나기를 기다린다."""
        self._worker.join(timeout)

    # 실행 -----------------------------------------------------------------------------------------------------

    def _loop(self) -> None:
        while not self._stopping.is_set():
            self._wake.wait(1.0)
            self._wake.clear()
            while not self._stopping.is_set():
                with self._lock:
                    job_id = self._queue.popleft() if self._queue else ""
                    job = self._jobs.get(job_id)
                    if job is not None:
                        job.state = "running"
                if job is None:
                    break
                try:
                    self._run_check(job) if job.kind == "check" else self._run_queries(job)
                except Exception as exc:  # noqa: BLE001 - 작업 하나의 오류가 대기열을 멈추지 않게 한다.
                    job.error = f"{type(exc).__name__}: {exc}"[:500]
                job.state = "cancelled" if job.cancel.is_set() else ("failed" if job.error else "done")

    def _run_check(self, job: Job) -> None:
        settings = self.store.settings()
        try:
            settings = settings.check()
            problem = ""
        except SettingsError as exc:
            problem = str(exc)
            # Aside 접점은 모델 없이도 본다. 모델을 부르는 점검은 설정이 갖춰질 때까지 건너뛴다.
            settings = replace(settings, model=settings.model or "-", effort=settings.effort or "-")
        report = check_aside(settings, with_exec=job.with_exec and not problem,
                             out_dir=self.store.runs_dir / "check", resolve=self.resolve)
        # 조사는 저장된 모델·추론 강도로만 돈다. 이것이 비면 Aside가 준비돼도 점검을 통과로 보이지 않게 한다.
        fallback = f" · 못 쓸 때 {settings.fallback_model}" if settings.fallback_model else ""
        report["checks"].insert(0, {"name": "settings", "ok": not problem,
                                    "detail": problem or f"{settings.model} · {settings.effort}{fallback}"})
        report["ok"] = bool(report["ok"]) and not problem
        job.report = report

    def _run_queries(self, job: Job) -> None:
        for item in job.items:
            if job.cancel.is_set():
                item.state = "cancelled"
                continue
            item.state = "running"
            try:
                settings = self.store.settings().check()
                query = make_query(item.title, item.venue, item.authors, item.year)
            except (SettingsError, QueryError) as exc:
                item.state, item.error = "failed", str(exc)
                continue

            def progress(stage: str, item: Item = item) -> None:
                item.stage = stage

            try:
                result = Run(query, settings, self.store.options(), resolve=self.resolve, cancel=job.cancel,
                             progress=progress).execute()
            except Exception as exc:  # noqa: BLE001 - 한 건의 오류가 다음 건을 막지 않게 한다.
                item.state, item.error = "failed", f"{type(exc).__name__}: {exc}"[:500]
                continue
            item.run_id = str((result.get("run") or {}).get("run_id") or "")
            item.result = summary(result, item.run_id)
            item.stage = ""
            if job.cancel.is_set() and result.get("status") == "failed":
                item.state = "cancelled"
            else:
                item.state = "failed" if result.get("status") == "failed" else "done"
                item.error = str(result.get("reason") or "") if item.state == "failed" else ""
