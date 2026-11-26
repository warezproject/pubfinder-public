"""창 실행 진입점. exe를 더블클릭하면 이 함수가 돈다.

- Windows 창(tkinter)을 연다. 네트워크 서버나 웹 브라우저는 쓰지 않는다.
- 이미 떠 있는 pubfinder가 있으면 새로 띄우지 않고 그 창을 앞으로 가져온다(같은 Aside를 함께 쓰므로 하나만 돈다).
- 창을 닫으면 진행 중인 조사의 Aside 세션을 멈추고 끝난다.
- 창 없는 exe는 표준출력이 없어서 로그를 데이터 폴더의 pubfinder.log에 남긴다.
"""

from __future__ import annotations

import argparse
import os
import sys
import traceback
from pathlib import Path
from typing import Any

from .desktop.controller import Controller
from .desktop.jobs import JobManager
from .desktop.storage import Store, default_data_dir

MUTEX_NAME = "Local\\pubfinder-desktop"
ERROR_ALREADY_EXISTS = 183
SW_RESTORE = 9


def _redirect_output(data_dir: Path) -> None:
    if sys.stdout is None or sys.stderr is None:
        log = open(data_dir / "pubfinder.log", "a", encoding="utf-8", buffering=1)  # noqa: SIM115 - 프로세스 끝까지 연다
        sys.stdout = sys.stdout or log
        sys.stderr = sys.stderr or log


def single_instance(name: str = MUTEX_NAME) -> Any:
    """이 사용자 세션에서 처음 뜬 pubfinder면 뮤텍스 핸들(프로세스가 끝날 때까지 잡아 둔다), 이미 떠 있으면 None.

    Windows가 아니거나 뮤텍스를 만들 수 없으면 막지 않는다(참 값을 돌려준다).
    """
    if os.name != "nt":
        return True
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
    handle = kernel32.CreateMutexW(None, False, name)
    if not handle:
        return True
    if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        kernel32.CloseHandle(handle)
        return None
    return handle


def bring_existing_window_to_front(title: str) -> bool:
    """제목이 같은 창을 찾아 앞으로 가져온다. 찾으면 True."""
    if os.name != "nt":
        return False
    import ctypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    hwnd = user32.FindWindowW(None, title)
    if not hwnd:
        return False
    user32.ShowWindow(hwnd, SW_RESTORE)
    user32.SetForegroundWindow(hwnd)
    return True


def _dpi_aware() -> None:
    """고해상도 화면에서 글자가 흐리지 않게 한다. 실패해도 그대로 연다."""
    if os.name != "nt":
        return
    import ctypes

    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except (AttributeError, OSError):
            pass


def run_window(data_dir: Path) -> int:
    import tkinter as tk
    from tkinter import messagebox

    from .desktop.window import WINDOW_TITLE, App, run_loop

    data_dir.mkdir(parents=True, exist_ok=True)
    lock = single_instance()
    if lock is None:
        if not bring_existing_window_to_front(WINDOW_TITLE):
            print("pubfinder가 이미 실행 중입니다.")
        return 0
    store = Store(data_dir)
    jobs = JobManager(store)
    controller = Controller(store, jobs)
    _dpi_aware()
    root = tk.Tk()

    def report_error(kind: type, value: BaseException, trace: Any) -> None:
        traceback.print_exception(kind, value, trace)
        messagebox.showerror("pubfinder 오류", f"{kind.__name__}: {value}", parent=root)

    root.report_callback_exception = report_error  # type: ignore[assignment]
    app = App(root, controller)
    try:
        run_loop(root)
    except KeyboardInterrupt:
        pass
    finally:
        if not app.closing:
            # 창을 거치지 않고 끝나는 경우(Ctrl+C 등)에도 진행 중인 조사의 Aside 세션을 멈춘다.
            controller.shutdown()
    del lock
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pubfinder-app", description="pubfinder 창을 연다")
    parser.add_argument("--data-dir", help="설정과 결과를 둘 폴더(기본: exe가 있는 폴더)")
    args = parser.parse_args(argv)
    data_dir = Path(args.data_dir).resolve() if args.data_dir else default_data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    _redirect_output(data_dir)
    return run_window(data_dir)


if __name__ == "__main__":
    raise SystemExit(main())
