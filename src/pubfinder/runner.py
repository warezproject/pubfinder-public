"""한 건 실행 흐름.

1. 입력으로 1차·2차 조사 지시문을 만든다. 본문은 `exec-<역할>/instructions.txt`에 쓰고, 세션에는 그 파일을 읽으라는
   짧은 지시만 보낸다(설정 `instructions_file`, host가 local일 때). 대조 지시문은 그대로 보낸다.
2. Aside 세션 두 개를 동시에 돌린다. 서로의 답은 보지 못한다. 결과는 세션 폴더의 JSON 파일로 받는다.
3. 2차 조사가 원문 근거를 하나도 남기지 않았으면 같은 지시로 한 번 다시 돌린다.
4. 두 결과의 근거 주소를 Aside 브라우저로 다시 열어 인용이 원문에 있는지 본다.
5. 웹을 쓰지 않는 Aside 세션 하나가 두 결과를 항목별로 대조한다. 답을 못 얻으면 문자열 일치 정책으로 판정한다.
6. 판정해 결과 JSON을 돌려준다.

실행 기록은 `out_dir/<run_id>/`에 남는다(지시문, 세션 실행 기록, 세션 기록 사본, 결과 파일, 캡처 요약).
차단 화면 사본은 남기지 않고, 받은 원문 본문은 `keep_captures`일 때만 남긴다.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import __version__
from .aside import transcript
from .aside.capture import Capturer
from .aside.client import AsideClient
from .aside.settings import AsideSettings
from .output import build_result, failure_result
from .prompts import (
    INSTRUCTIONS_FILE,
    UNREADABLE_MARKER,
    build_pointer_prompt,
    build_primary_prompt,
    build_review_prompt,
    with_common_rules,
)
from .query import Query
from .quotes import capture_summary
from .reconcile import build_reconcile_prompt, first_json_object, parse_reconcile
from .results import ResultError, evidence_urls, has_evidence, parse_primary_result, parse_review_result
from .sources import DEFAULT_EXCLUDED_DOMAINS
from .verdict import judge, make_bundle, report

RECONCILE_MIN_SECONDS = 30.0


@dataclass(frozen=True)
class RunOptions:
    out_dir: Path = Path("pubfinder-runs")
    korean_place_as_korea: bool = True
    excluded_domains: tuple[str, ...] = DEFAULT_EXCLUDED_DOMAINS
    # 한 건 전체 상한(초). 세션 실행·재시도·다시 열기·대조가 모두 이 안에서 돈다.
    run_deadline: float = 3600.0
    keep_captures: bool = False
    extra: dict = field(default_factory=dict)


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=1, default=str), encoding="utf-8")


class Run:
    def __init__(self, query: Query, settings: AsideSettings, options: RunOptions, *,
                 client: AsideClient | None = None, resolve: Callable[..., Any] | None = None,
                 cancel: threading.Event | None = None,
                 progress: Callable[[str], None] | None = None) -> None:
        self.query = query
        # 화면에 보일 진행 단계(한국어 한 줄). 화면 없이 돌릴 때는 아무것도 하지 않는다.
        self._progress_callback = progress
        self.settings = settings
        self.options = options
        self.client = client or AsideClient(settings)
        self.capturer = Capturer(self.client, resolve=resolve)
        self.cancel = cancel or threading.Event()
        self.run_id = f"{time.strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:8]}"
        self.review_run_id = str(uuid.uuid4())
        self.dir = Path(options.out_dir) / self.run_id
        self.clock = self.client.clock
        self.started = self.clock()
        self.deadline = self.started + float(options.run_deadline)
        self.sessions: dict[str, str] = {}
        self.records: dict[str, dict] = {}
        self.warnings: list[str] = []
        self._lock = threading.Lock()

    def progress(self, stage: str) -> None:
        if self._progress_callback is not None:
            try:
                self._progress_callback(stage)
            except Exception:  # noqa: BLE001 - 진행 표시 오류가 조사를 멈추지 않게 한다.
                pass

    # 세션 -----------------------------------------------------------------------------------------------------

    def result_relpath(self, role: str) -> str:
        return f"artifacts/cpf/{self.run_id}/{role}/result.json"

    def remaining(self) -> float:
        return self.deadline - self.clock()

    def _to_send(self, exec_dir: Path, prompt: str, from_file: bool) -> str:
        """세션에 보낼 지시. `from_file`이면 본문을 실행 기록 폴더의 파일로 쓰고 그 파일을 읽으라는 짧은 지시를 돌려준다.

        세션이 이 PC의 파일을 읽어야 하므로 host가 local일 때만 그렇게 한다. 대체 모델로 다시 돌 때도 같은 파일을 읽는다.
        """
        full = with_common_rules(prompt)
        if not (from_file and self.settings.instructions_file and self.settings.host == "local"):
            return full
        exec_dir.mkdir(parents=True, exist_ok=True)
        path = (exec_dir / INSTRUCTIONS_FILE).resolve()
        path.write_text(full, encoding="utf-8")
        return build_pointer_prompt(self.query.source_input(), run_id=self.run_id,
                                    instructions_path=path.as_posix(), instructions_chars=len(full))

    def _session(self, role: str, prompt: str, *, timeout: float, expect_result: bool = True,
                 from_file: bool = False) -> dict[str, Any]:
        """세션 하나를 돌리고 기록·결과 파일을 모은다. 대조 세션은 결과 파일 없이 마지막 답으로 끝난다."""
        exec_dir = self.dir / f"exec-{role}"
        timeout = min(timeout, self.remaining())
        if timeout <= 0 or self.cancel.is_set():
            record = {"termination": "deadline" if timeout <= 0 else "cancelled", "session_id": ""}
            collected = {"transcript": transcript.summarize(None), "result_file": ""}
        else:
            def remember(session_id: str) -> None:
                with self._lock:
                    self.sessions[role] = session_id

            # 한도·잔액·연결 문제로 거절되면 설정의 대체 모델로 한 번 다시 돈다(record의 fallback_from에 처음 시도).
            record, collected = self.client.run_session(self._to_send(exec_dir, prompt, from_file), exec_dir,
                                                        self.result_relpath(role), timeout=timeout,
                                                        on_session=remember, cancel=self.cancel)
        summary = collected.get("transcript") or {}
        outcome = {"role": role, "record": record, "result_file": collected.get("result_file") or "",
                   "result_source": collected.get("result_source") or "", "transcript": summary,
                   "failure": transcript.failure_status(record, summary)}
        if expect_result and not outcome["failure"] and not outcome["result_file"]:
            unreadable = UNREADABLE_MARKER in str(summary.get("final_text") or "")
            outcome["failure"] = "instructions_unreadable" if unreadable else "result_missing"
        outcome["stdout"] = transcript.read_text(Path(record.get("exec_dir") or exec_dir) / "stdout.txt")[-20000:]
        with self._lock:
            self.records[role] = {
                "model": record.get("model", self.settings.model), "fallback_from": record.get("fallback_from"),
                "session_id": record.get("session_id", ""), "termination": record.get("termination"),
                "exit_code": record.get("exit_code"), "elapsed_seconds": record.get("elapsed_seconds"),
                "failure": outcome["failure"], "result_source": outcome["result_source"],
                "models": summary.get("models"), "providers": summary.get("providers"),
                "model_verification": summary.get("model_verification"), "usage": summary.get("usage"),
                "errors": summary.get("errors"), "error_kind": summary.get("error_kind"),
                "tool_calls": len(summary.get("tool_calls") or []),
            }
        _write(exec_dir / "record.json", record)
        return outcome

    def _sessions(self, steps: list[tuple[str, str]], timeout: float, *, expect_result: bool = True,
                  from_file: bool = False) -> dict[str, dict]:
        pool = ThreadPoolExecutor(max_workers=len(steps), thread_name_prefix="cpf-session")
        try:
            futures = {role: pool.submit(self._session, role, prompt, timeout=timeout, expect_result=expect_result,
                                         from_file=from_file)
                       for role, prompt in steps}
            return {role: future.result() for role, future in futures.items()}
        except BaseException:
            # Ctrl+C 등. 세션 감독 쪽이 1초 안에 보고 자기 세션을 멈추게 한 뒤 끝나기를 기다린다.
            self.cancel.set()
            raise
        finally:
            pool.shutdown(wait=True)

    def stop_all(self) -> list[str]:
        stopped = []
        for session_id in list(self.sessions.values()):
            try:
                if self.client.stop_if_running(session_id):
                    stopped.append(session_id)
            except Exception:  # noqa: BLE001 - 정리 단계의 오류가 원래 오류를 가리지 않게 한다.
                continue
        return stopped

    # 흐름 -----------------------------------------------------------------------------------------------------

    def _run_info(self, preflight: dict | None = None) -> dict:
        return {
            "run_id": self.run_id, "program_version": __version__, "out_dir": str(self.dir),
            "aside_version": (preflight or {}).get("version", ""), "model": self.settings.model,
            # 대체 모델로 바꿨으면 {"from", "to", "reason", "message"}. 세션마다 쓴 모델은 sessions.<역할>.model.
            "model_fallback": self.client.fallback,
            "effort": self.settings.effort, "account": self.settings.account, "host": self.settings.host,
            "elapsed_seconds": round(self.clock() - self.started, 3), "sessions": dict(self.records),
            "settings": {"korean_place_as_korea": self.options.korean_place_as_korea,
                         "excluded_domains": list(self.options.excluded_domains),
                         "run_deadline": self.options.run_deadline},
        }

    def _finish(self, result: dict) -> dict:
        _write(self.dir / "result.json", result)
        return result

    def _note_fallback(self) -> None:
        fallback = self.client.fallback
        if fallback:
            why = transcript.REFUSAL_TEXT.get(fallback.get("reason", ""), "공급자 거절")
            note = (f"{fallback['from']} 모델을 쓸 수 없어({why}) 대체 모델 {fallback['to']}로 바꿔 조사했습니다"
                    f"({fallback['message']}).")
            if note not in self.warnings:
                self.warnings.append(note)

    def _fail(self, reason: str, preflight: dict | None = None, partial: dict | None = None) -> dict:
        self._note_fallback()
        return self._finish(failure_result(self.query, reason=reason, run=self._run_info(preflight),
                                           warnings=self.warnings, partial=partial))

    def execute(self) -> dict:
        try:
            return self._execute()
        except BaseException:
            self.cancel.set()
            self.stop_all()
            raise

    def _execute(self) -> dict:
        self.dir.mkdir(parents=True, exist_ok=True)
        source, fingerprint = self.query.source_input(), self.query.fingerprint()
        options = self.options
        _write(self.dir / "request.json", {"query": source, "input_fingerprint": fingerprint,
                                           "review_run_id": self.review_run_id, **self._run_info()})
        self.progress("Aside 준비 확인 중")
        preflight = self.client.preflight()
        _write(self.dir / "preflight.json", preflight)
        if preflight.get("status") != "ready":
            return self._fail(f"aside_unavailable:{preflight.get('reason', '')}", preflight)
        if not self.settings.profile:
            profile = self.client.discover_profile()
            if profile:
                self.client.settings = self.settings = _replace(self.settings, profile=profile)
        common = {"korean_place_as_korea": options.korean_place_as_korea,
                  "excluded_domains": options.excluded_domains}
        primary_prompt = build_primary_prompt(source, fingerprint, run_id=self.run_id,
                                              result_path="./" + self.result_relpath("primary"), **common)

        def review_prompt(role: str) -> str:
            return build_review_prompt(source, self.review_run_id, run_id=self.run_id,
                                       result_path="./" + self.result_relpath(role), **common)

        self.progress("1차·2차 조사 중(보통 1~4분)")
        outcomes = self._sessions([("primary", primary_prompt), ("review", review_prompt("review"))],
                                  self.settings.research_timeout, from_file=True)
        first = outcomes["primary"]
        if first["failure"]:
            return self._fail(f"primary:{first['failure']}", preflight)
        try:
            primary = parse_primary_result(Path(first["result_file"]).read_bytes(), run_id=self.run_id,
                                           input_fingerprint=fingerprint, excluded=options.excluded_domains)
        except (OSError, ResultError) as exc:
            return self._fail(f"primary:invalid_result:{exc}", preflight)
        self.warnings += [f"1차 결과: {note}" for note in primary["notes"]]
        second = outcomes["review"]
        review, review_role, retry = self._review(second, review_prompt)
        if review is None:
            return self._fail(f"review:{retry.get('status') or second['failure']}", preflight,
                              partial={"primary": primary})
        primary_report = report("primary", primary["observations"], run_id=self.run_id,
                                session_id=self.sessions.get("primary", ""))
        review_report = report("review", review["observations"], run_id=self.run_id,
                               session_id=self.sessions.get(review_role, ""), review_run_id=self.review_run_id)
        urls = evidence_urls(primary["observations"], review["observations"])
        self.progress(f"근거 주소 {min(len(urls), 20)}개 다시 여는 중")
        captures = self.capturer.capture_all(urls, deadline=self.deadline, clock=self.clock)
        summary = capture_summary([primary_report, review_report], captures)
        self._save_captures(captures, summary)
        self.progress("두 조사 대조 중")
        decisions, reconcile_status = self._reconcile(source, primary, primary_report, review_report)
        bundle = make_bundle(source, outcome=primary["outcome"], review_run_id=self.review_run_id,
                             primary=primary_report, review=review_report, captures=summary, reconcile=decisions,
                             korean_place_as_korea=options.korean_place_as_korea,
                             excluded_domains=tuple(options.excluded_domains))
        verdict = judge(bundle)
        if reconcile_status != "parsed":
            self.warnings.append(f"대조 답을 쓰지 못해 문자열 일치 정책으로 판정했습니다({reconcile_status}).")
        blocked = [item["url"] for item in summary if item["status"] == "blocked"]
        if blocked:
            self.warnings.append(f"차단돼 다시 열지 못한 근거 주소 {len(blocked)}개가 있습니다.")
        self._note_fallback()
        run = {**self._run_info(preflight), "reconcile_status": reconcile_status, "review_retry": retry,
               "primary_outcome_reason": primary["reason"], "trail": primary["trail"]}
        return self._finish(build_result(self.query, bundle=bundle, verdict=verdict, run=run, captures=summary,
                                         warnings=self.warnings))

    def _review(self, second: dict, review_prompt: Callable[[str], str]) -> tuple[dict | None, str, dict]:
        """2차 결과를 읽는다. 원문 근거가 하나도 없으면 같은 입력으로 한 번 다시 조사한다."""
        retry: dict[str, Any] = {}
        review = None
        if not second["failure"]:
            try:
                review = parse_review_result(Path(second["result_file"]).read_bytes(), run_id=self.run_id,
                                             review_run_id=self.review_run_id,
                                             excluded=self.options.excluded_domains)
            except (OSError, ResultError) as exc:
                retry["status"] = f"invalid_result:{exc}"
                return None, "review", retry
        else:
            return None, "review", {"status": second["failure"]}
        if has_evidence(review["observations"]):
            self.warnings += [f"2차 결과: {note}" for note in review["notes"]]
            return review, "review", retry
        # 원문을 하나도 열지 않고 끝난 확인은 1차가 확인한 값까지 막는다. 한 번만 다시 확인한다.
        retry = {"reason": "review_without_evidence"}
        self.progress("2차 조사가 근거를 남기지 않아 다시 조사 중")
        outcome = self._sessions([("review-retry", review_prompt("review-retry"))],
                                 self.settings.research_timeout, from_file=True)["review-retry"]
        if outcome["failure"]:
            retry["status"] = outcome["failure"]
            return review, "review", retry
        try:
            retried = parse_review_result(Path(outcome["result_file"]).read_bytes(), run_id=self.run_id,
                                          review_run_id=self.review_run_id, excluded=self.options.excluded_domains)
        except (OSError, ResultError) as exc:
            retry["status"] = f"invalid_result:{exc}"
            return review, "review", retry
        retry.update(status="completed", has_evidence=has_evidence(retried["observations"]))
        self.warnings += [f"2차 재조사 결과: {note}" for note in retried["notes"]]
        return retried, "review-retry", retry

    def _reconcile(self, source: dict, primary: dict, primary_report: dict, review_report: dict
                   ) -> tuple[dict | None, str]:
        try:
            prompt = build_reconcile_prompt(source, primary_report, review_report, primary_outcome=primary["outcome"],
                                            korean_place_as_korea=self.options.korean_place_as_korea)
        except ValueError as exc:
            _write(self.dir / "reconcile.json", {"status": str(exc)})
            return None, str(exc)
        timeout = min(self.settings.reconcile_timeout, self.remaining())
        if timeout < RECONCILE_MIN_SECONDS:
            _write(self.dir / "reconcile.json", {"status": "skipped_deadline"})
            return None, "skipped_deadline"
        outcome = self._sessions([("reconcile", prompt)], timeout, expect_result=False)["reconcile"]
        record: dict[str, Any] = {"timeout_seconds": timeout}
        decisions = None
        if outcome["failure"]:
            status = f"failed:{outcome['failure']}"
        else:
            # 마지막 답은 세션 기록에서 읽고, 세션 폴더가 이미 지워졌으면 `aside exec` 표준출력(답만 나온다)에서 읽는다.
            text = str((outcome["transcript"] or {}).get("final_text") or "") or outcome["stdout"]
            try:
                decisions = parse_reconcile(first_json_object(text))
                status = "parsed"
            except ValueError as exc:
                status = f"invalid:{exc}"
                record["final_text"] = text[:4000]
        record.update(status=status, decisions=decisions)
        _write(self.dir / "reconcile.json", record)
        return decisions, status

    def _save_captures(self, captures: dict[str, dict], summary: list[dict]) -> None:
        folder = self.dir / "captures"
        _write(folder / "summary.json", summary)
        if not self.options.keep_captures:
            return
        for capture in captures.values():
            body = capture.get("body")
            if capture.get("status") == "fetched" and isinstance(body, bytes):
                suffix = ".pdf" if body.startswith(b"%PDF") else ".html"
                (folder / f"{capture['sha256']}{suffix}").write_bytes(body)


def _replace(settings: AsideSettings, **changes: Any) -> AsideSettings:
    from dataclasses import replace

    return replace(settings, **changes)


def run_query(query: Query, settings: AsideSettings, options: RunOptions | None = None, **kwargs: Any) -> dict:
    """한 건 실행. 결과 JSON(dict)을 돌려주고 `out_dir/<run_id>/result.json`에도 남긴다."""
    return Run(query, settings.check(), options or RunOptions(), **kwargs).execute()
