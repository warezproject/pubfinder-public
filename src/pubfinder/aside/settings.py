"""Aside 실행 설정. 모델과 추론 강도는 기본값이 없고 사용자가 정해야 한다(2026-11-15 결정)."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path


def default_executable() -> str:
    base = os.getenv("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    windows = Path(base) / "Aside" / "CLI" / "current" / "aside.exe"
    return str(windows) if os.name == "nt" else "aside"


class SettingsError(ValueError):
    """설정이 빠졌거나 틀렸다. 메시지는 사용자에게 그대로 보인다."""


@dataclass(frozen=True)
class AsideSettings:
    model: str
    effort: str
    executable: str = field(default_factory=default_executable)
    # 공급자가 한도·잔액·연결 문제로 `model`을 거절하면 그 세션을 한 번 다시 돌릴 모델(2026-11-25 사용자 지시). 비우면 바꾸지 않는다.
    fallback_model: str = ""
    # 실행 파일 앞에 붙일 명령(시험에서 가짜 CLI를 `python fake.py`로 부를 때 쓴다).
    launcher: tuple[str, ...] = ()
    account: str = "u0"
    host: str = "local"
    profile: str = ""
    speed: str = "default"
    home: str = field(default_factory=lambda: str(Path.home() / ".aside"))
    preflight_timeout: float = 30.0
    research_timeout: float = 1200.0
    reconcile_timeout: float = 180.0
    capture_timeout: float = 150.0
    stop_timeout: float = 30.0
    # 같은 프로세스의 `aside exec` 시작 사이 최소 간격(초). 동시 세션이 계정 자격 파일을 함께 읽다 잠금 오류로 끝난
    # 기록이 있어 시작 시각만 벌린다. 0이면 끈다.
    launch_interval: float = 2.0
    # 조사 지시 본문을 실행 기록 폴더의 파일로 쓰고 세션에는 그 파일을 읽으라는 짧은 지시만 보낸다(2026-11-25 사용자 지시).
    # 세션이 이 PC의 파일을 읽어야 하므로 host가 "local"일 때만 쓴다. 끄면 지시문 전체를 명령줄로 보낸다.
    instructions_file: bool = True
    autostart: bool = False
    app_path: str = ""
    autostart_wait: float = 45.0

    def check(self) -> "AsideSettings":
        if not self.model.strip():
            raise SettingsError("Aside 모델을 정하세요(--model 또는 설정 파일 [aside] model). 기본값은 없습니다.")
        if not self.effort.strip():
            raise SettingsError("추론 강도를 정하세요(--effort 또는 설정 파일 [aside] effort). 기본값은 없습니다.")
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,64}", self.account):
            raise SettingsError("Aside 계정 이름이 올바르지 않습니다")
        for name in ("preflight_timeout", "research_timeout", "reconcile_timeout", "capture_timeout",
                     "stop_timeout"):
            if float(getattr(self, name)) <= 0:
                raise SettingsError(f"{name}은 0보다 커야 합니다")
        return self

    def command(self, *args: str) -> list[str]:
        return [*self.launcher, str(self.executable), *args]

    def account_directory(self) -> Path:
        match = re.fullmatch(r"u(\d+)(?::.*)?", self.account)
        return Path(self.home) / "u" / (match.group(1) if match else self.account)

    def target_args(self) -> list[str]:
        """계정·호스트·(알면) 브라우저 프로필 인자."""
        args = ["--account", self.account, "--host", self.host]
        return args + (["--profile", self.profile] if self.profile else [])
