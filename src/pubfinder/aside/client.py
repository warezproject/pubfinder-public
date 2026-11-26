"""Aside CLI 호출. 세션 실행·감독·중지, 세션 기록 수집, REPL 실행, 사전 확인을 맡는다.

알아 둘 것
- 지시문은 표준 입력이 아니라 명령줄 인자로 넘긴다(Windows 명령줄 상한 32,767자).
- 세션 ID는 표준오류의 `created new session: <ID>` 줄에서 읽는다.
- 결과는 표준출력이 아니라 세션 폴더(`artifacts/…`)에서 읽는다. 세션 폴더에 없으면 계정 폴더 아래 같은 경로를 본다.
- 세션을 띄우지 못해도 종료 코드가 0일 수 있다. 결과 파일이 없으면 세션 기록과 표준출력으로 사유를 가린다.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable

from . import transcript
from .settings import AsideSettings

SESSION_LINE = re.compile(r"created new session:\s*([A-Za-z0-9_-]{4,64})")
SESSION_ID = re.compile(r"[A-Za-z0-9_-]{4,64}")
_NOT_RUNNING_MARKERS = ("isn't running", "is not running", "Start Aside Browser", "is not connected to the daemon")
_IDLE_STATES = {"idle", "interrupted", "errored", "absent", "unknown", "stopped", "completed", "archived"}
MAX_RESULT_BYTES = 5 * 1024 * 1024
# Windows 명령줄 상한은 32,767자다(따옴표 이스케이프 포함).
MAX_COMMAND_CHARS = 32000
READY_SCRIPT = "await listBrowserTabs(); console.log(JSON.stringify({cpf_ready: true}))"


def _ready_reply(stdout: str) -> bool:
    for line in (stdout or "").split("\n"):
        line = line.strip()
        if line.startswith("{"):
            try:
                if json.loads(line).get("cpf_ready") is True:
                    return True
            except (ValueError, AttributeError):
                continue
    return False


def _not_running(result: Any) -> bool:
    text = transcript.clean(str(getattr(result, "stdout", "") or "") + "\n" + str(getattr(result, "stderr", "") or ""))
    return any(marker in text for marker in _NOT_RUNNING_MARKERS)


class AsideClient:
    def __init__(self, settings: AsideSettings, *, run: Callable[..., Any] = subprocess.run,
                 popen: Callable[..., Any] = subprocess.Popen, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], Any] = time.sleep) -> None:
        self.settings = settings
        self._run_process = run
        self._popen = popen
        self.clock = clock
        self.sleep = sleep
        self._launch_lock = threading.Lock()
        self._last_launch: float | None = None
        self._model_lock = threading.Lock()
        # 주 모델을 쓸 수 없어 대체 모델로 바꿨으면 {"from", "to", "reason", "message"}.
        # 이 클라이언트가 띄우는 다음 세션은 처음부터 대체 모델로 돈다.
        self.fallback: dict[str, str] | None = None

    def current_model(self) -> str:
        """새 세션에 쓸 모델. 대체 모델로 바꾼 뒤에는 대체 모델."""
        with self._model_lock:
            return self.fallback["to"] if self.fallback else self.settings.model

    def _fallback_for(self, failed_model: str, summary: dict[str, Any]) -> str:
        """공급자가 `failed_model`을 한도·잔액·연결 문제로 거절했으면 다시 돌릴 대체 모델. 아니면 빈 문자열."""
        wanted = self.settings.fallback_model.strip()
        reason = transcript.provider_refusal(summary)
        if not wanted or failed_model == wanted or not reason:
            return ""
        with self._model_lock:
            if self.fallback is None:
                errors = summary.get("errors") or [""]
                self.fallback = {"from": failed_model, "to": wanted, "reason": reason,
                                 "message": transcript.provider_message(errors[0])}
            return self.fallback["to"]

    # 짧은 명령 -------------------------------------------------------------------------------------------------

    def _run(self, args: list[str], timeout: float) -> Any:
        # 창 없는 exe에서 부를 때 콘솔 창이 깜빡이지 않게 한다(Windows가 아니면 0).
        return self._run_process(self.settings.command(*args), capture_output=True, text=True, encoding="utf-8",
                                 errors="replace", timeout=max(1.0, timeout), shell=False, check=False,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))

    def version(self) -> str:
        try:
            result = self._run(["--version"], self.settings.preflight_timeout)
        except (OSError, subprocess.TimeoutExpired):
            return ""
        text = transcript.clean(result.stdout).strip()
        return text.splitlines()[0][:100] if result.returncode == 0 and text else ""

    def discover_profile(self) -> str:
        """연결된 브라우저 프로필이 정확히 하나일 때만 그 ID. 이름으로 추측하지 않는다."""
        if self.settings.profile:
            return self.settings.profile
        try:
            reply = self._run(["host", "profiles", "--host", self.settings.host], self.settings.preflight_timeout)
        except (OSError, subprocess.TimeoutExpired):
            return ""
        ids = re.findall(r"^([a-f0-9-]{36})\s+.+\s+connected\s*$", transcript.clean(reply.stdout), re.MULTILINE)
        return ids[0] if reply.returncode == 0 and len(ids) == 1 else ""

    def _start_app(self) -> bool:
        app = Path(self.settings.app_path) if self.settings.app_path else None
        if app is None or not app.is_file():
            return False
        flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        try:
            self._popen([str(app)], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                        close_fds=True, creationflags=flags)
        except OSError:
            return False
        return True

    def preflight(self) -> dict[str, Any]:
        """CLI·계정 로그인·브라우저 연결을 확인한다. 설정에서 켠 경우에만 꺼진 Aside 앱을 한 번 시작한다."""
        settings = self.settings
        if not settings.launcher and not Path(settings.executable).is_file() and shutil.which(settings.executable) is None:
            return {"status": "unavailable", "reason": "aside_executable_missing"}
        version = self.version()
        if not version:
            return {"status": "unavailable", "reason": "aside_version_failed"}
        app_started = False
        deadline = self.clock() + settings.preflight_timeout
        while True:
            reason = ""
            try:
                account = self._run(["account", "status", "--account", settings.account],
                                    max(1.0, deadline - self.clock()))
            except subprocess.TimeoutExpired:
                reason = "aside_preflight_timeout"
            except OSError as exc:
                return {"status": "unavailable", "reason": f"aside_launch_failed:{type(exc).__name__}"}
            else:
                if _not_running(account):
                    reason = "aside_not_running"
                elif account.returncode != 0 or not any(
                        settings.account in line.split() and "signed in" in line
                        for line in transcript.clean(account.stdout).splitlines()):
                    return {"status": "unavailable", "reason": "aside_account_not_signed_in", "version": version}
                else:
                    ready = self.repl(READY_SCRIPT, timeout=max(1.0, deadline - self.clock()), recover=False)
                    if _ready_reply(ready["stdout"]):
                        return {"status": "ready", "version": version, "account": settings.account,
                                "host": settings.host, "app_started": app_started}
                    reason = ready["error"] or "aside_browser_unavailable"
            if app_started or settings.host != "local" or not settings.autostart or not self._start_app():
                if reason == "aside_not_running":
                    reason += ":Aside 브라우저를 켜고 다시 실행하세요"
                return {"status": "unavailable", "reason": reason, "version": version, "app_started": app_started}
            app_started = True
            deadline = self.clock() + settings.autostart_wait
            self.sleep(5.0)

    # REPL -----------------------------------------------------------------------------------------------------

    def repl(self, script: str, *, timeout: float | None = None, recover: bool = True) -> dict[str, Any]:
        """`aside repl` 한 번. {"stdout", "returncode", "error"}. 앱이 꺼진 것으로 보이면 사전 확인 뒤 한 번 더 한다."""
        timeout = float(timeout or self.settings.capture_timeout)
        for attempt in range(2):
            try:
                result = self._run(["repl", *self.settings.target_args(), script], timeout)
            except subprocess.TimeoutExpired:
                return {"stdout": "", "returncode": None, "error": "aside_repl_timeout"}
            except OSError:
                return {"stdout": "", "returncode": None, "error": "aside_repl_launch_failed"}
            if recover and attempt == 0 and result.returncode != 0 and self.settings.autostart and _not_running(result):
                if self.preflight().get("status") == "ready":
                    continue
            break
        error = "" if result.returncode == 0 else ("aside_not_running" if _not_running(result) else "aside_repl_failed")
        return {"stdout": transcript.clean(result.stdout), "returncode": result.returncode, "error": error}

    # 세션 -----------------------------------------------------------------------------------------------------

    def _wait_launch_turn(self) -> float:
        interval = float(self.settings.launch_interval)
        if interval <= 0:
            return 0.0
        with self._launch_lock:
            now = self.clock()
            waited = 0.0
            if self._last_launch is not None and 0 <= now - self._last_launch < interval:
                waited = interval - (now - self._last_launch)
                self.sleep(waited)
                now = self.clock()
            self._last_launch = now
            return waited

    def exec_argv(self, prompt: str, model: str | None = None) -> list[str]:
        s = self.settings
        return s.command("exec", *s.target_args(), "--model", model or s.model, "--effort", s.effort,
                         "--speed", s.speed, prompt)

    def run_session(self, prompt: str, exec_dir: Path, result_relpath: str, *, timeout: float,
                    on_session: Callable[[str], None] | None = None,
                    cancel: threading.Event | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
        """세션 하나를 돌리고 기록·결과 파일을 모은다(record, collected).

        공급자가 한도·잔액·연결 문제로 모델을 거절했고 설정에 대체 모델이 있으면, 남은 시간 안에서 대체 모델로 한 번 다시 돌린다.
        다시 돌린 기록은 `exec_dir/fallback/`에 남고, record의 `fallback_from`에 처음 시도가 남는다.
        """
        started = self.clock()
        model = self.current_model()
        record = self.exec_session(prompt, exec_dir, timeout=timeout, on_session=on_session, cancel=cancel,
                                   model=model)
        collected = self.collect(record, exec_dir, result_relpath)
        if cancel is not None and cancel.is_set():
            return record, collected
        fallback = self._fallback_for(model, collected.get("transcript") or {})
        left = float(timeout) - (self.clock() - started)
        if not fallback or left <= 0:
            return record, collected
        first = {"model": model, "session_id": record.get("session_id", ""),
                 "errors": (collected.get("transcript") or {}).get("errors") or []}
        retry_dir = Path(exec_dir) / "fallback"
        record = self.exec_session(prompt, retry_dir, timeout=left, on_session=on_session, cancel=cancel,
                                   model=fallback)
        record["fallback_from"] = first
        return record, self.collect(record, retry_dir, result_relpath)

    def exec_session(self, prompt: str, run_dir: Path, *, timeout: float,
                     on_session: Callable[[str], None] | None = None,
                     cancel: threading.Event | None = None, model: str | None = None) -> dict[str, Any]:
        """`aside exec` 하나를 실행하고 세션 ID·종료 상태를 기록한다. 시간 초과·취소면 이 세션만 멈춘다."""
        run_dir = Path(run_dir)
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "prompt.txt").write_text(prompt, encoding="utf-8")
        model = model or self.settings.model
        argv = self.exec_argv(prompt, model)
        command_chars = len(subprocess.list2cmdline(argv))
        record: dict[str, Any] = {
            "model": model,
            "exec_dir": str(run_dir),
            "command_chars": command_chars,
            "argv": argv[:-1] + ["<prompt.txt>"],
            "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
            "prompt_chars": len(prompt),
            "started_at": time.time(),
            "session_id": "",
            "exit_code": None,
            "termination": "",
            "stop": None,
        }
        if os.name == "nt" and command_chars > MAX_COMMAND_CHARS:
            # 지시문은 명령줄 인자로만 넘길 수 있다. 상한을 넘으면 세션을 띄우지 않는다.
            record.update(termination="launch_failed", error_type="command_too_long", ended_at=time.time())
            return record
        started = self.clock()
        deadline = started + max(1.0, float(timeout))
        record["launch_wait_seconds"] = round(self._wait_launch_turn(), 3)
        stdout_path, stderr_path = run_dir / "stdout.txt", run_dir / "stderr.txt"

        def find_session() -> None:
            if not record["session_id"]:
                found = SESSION_LINE.search(transcript.clean(transcript.read_text(stderr_path)))
                if found:
                    record["session_id"] = found.group(1)
                    if on_session is not None:
                        on_session(record["session_id"])

        with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
            try:
                process = self._popen(argv, stdout=stdout, stderr=stderr, stdin=subprocess.DEVNULL, shell=False,
                                      creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            except OSError as exc:
                record.update(termination="launch_failed", error_type=type(exc).__name__, ended_at=time.time())
                return record
            reason = ""
            while True:
                find_session()
                code = process.poll()
                if code is not None:
                    record["exit_code"] = code
                    break
                if cancel is not None and cancel.is_set():
                    reason = "cancelled"
                    break
                if self.clock() >= deadline:
                    reason = "timeout"
                    break
                self.sleep(1.0)
            if reason:
                find_session()
                if record["session_id"]:
                    record["stop"] = self.stop(record["session_id"])
                try:
                    record["exit_code"] = process.wait(timeout=self.settings.stop_timeout)
                except subprocess.TimeoutExpired:
                    process.terminate()
                    try:
                        record["exit_code"] = process.wait(timeout=self.settings.stop_timeout)
                    except subprocess.TimeoutExpired:
                        process.kill()
                record["termination"] = reason
            else:
                record["termination"] = "normal" if record["exit_code"] == 0 else "error"
        find_session()
        record["ended_at"] = time.time()
        record["elapsed_seconds"] = round(self.clock() - started, 3)
        return record

    def stop(self, session_id: str) -> dict[str, Any]:
        """이 프로그램이 띄운 세션 하나만 멈춘다."""
        if not SESSION_ID.fullmatch(session_id or ""):
            return {"status": "invalid_session_id"}
        try:
            result = self._run(["session", "stop", "--account", self.settings.account, session_id],
                               self.settings.stop_timeout)
        except subprocess.TimeoutExpired:
            return {"status": "stop_timeout"}
        except OSError as exc:
            return {"status": "stop_failed", "error_type": type(exc).__name__}
        return {"status": "stopped" if result.returncode == 0 else "stop_failed", "exit_code": result.returncode}

    def session_status(self, session_id: str) -> str:
        if not SESSION_ID.fullmatch(session_id or ""):
            return "unknown"
        try:
            result = self._run(["session", "list", "--account", self.settings.account], self.settings.preflight_timeout)
        except (OSError, subprocess.TimeoutExpired):
            return "unknown"
        for line in transcript.clean(result.stdout).splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[0] == session_id:
                return parts[1]
        return "absent"

    def stop_if_running(self, session_id: str) -> bool:
        if session_id and self.session_status(session_id) not in _IDLE_STATES:
            self.stop(session_id)
            return True
        return False

    def session_folder(self, session_id: str) -> Path | None:
        """세션 ID로 끝나는 폴더가 정확히 하나일 때만."""
        if not SESSION_ID.fullmatch(session_id or ""):
            return None
        root = self.settings.account_directory() / "sessions"
        if not root.is_dir():
            return None
        matches = [path for path in root.iterdir() if path.is_dir() and path.name.endswith("_" + session_id)]
        return matches[0] if len(matches) == 1 else None

    def collect(self, record: dict[str, Any], exec_dir: Path, result_relpath: str) -> dict[str, Any]:
        """세션 기록과 이 역할의 결과 파일만 `exec_dir/session/`으로 복사한다. 원래 세션 폴더는 바꾸지 않는다."""
        exec_dir = Path(exec_dir)
        destination = exec_dir / "session"
        destination.mkdir(parents=True, exist_ok=True)
        session_id = str(record.get("session_id") or "")
        folder = self.session_folder(session_id) if session_id else None
        collected: dict[str, Any] = {"session_id": session_id, "session_folder": folder.name if folder else "",
                                     "result_file": "", "result_source": ""}
        messages = None
        if folder is not None:
            source = folder / "messages.jsonl"
            if source.is_file() and not source.is_symlink():
                messages = destination / "messages.jsonl"
                shutil.copyfile(source, messages)
        target = destination / "result.json"
        candidates = ([("session_folder", folder / result_relpath)] if folder is not None else []) + [
            ("account_folder", self.settings.account_directory() / result_relpath)]
        for label, path in candidates:
            try:
                if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_RESULT_BYTES:
                    continue
                if label == "account_folder" and path.stat().st_mtime < float(record.get("started_at") or 0) - 1:
                    continue
                shutil.copyfile(path, target)
            except OSError:
                continue
            collected.update(result_file=str(target), result_source=label)
            break
        summary = transcript.summarize(messages)
        summary["model_verification"] = transcript.model_verification(summary,
                                                                      record.get("model") or self.settings.model)
        if record.get("termination") in {"normal", "error"} and not collected["result_file"]:
            summary = transcript.adopt_exec_errors(summary, transcript.read_text(exec_dir / "stdout.txt"),
                                                   transcript.read_text(exec_dir / "stderr.txt"))
        collected["transcript"] = summary
        (destination / "collect.json").write_text(
            json.dumps({k: v for k, v in collected.items() if k != "transcript"}
                       | {"transcript": {k: v for k, v in summary.items() if k != "final_text"}},
                       ensure_ascii=False, indent=1), encoding="utf-8")
        return collected
