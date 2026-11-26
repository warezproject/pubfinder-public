"""설정 파일(TOML) 읽기. 명령줄 값이 설정 파일보다 먼저다.

```toml
[aside]
model = "provider/model"   # 필수. 기본값 없음
effort = "medium"          # 필수. 기본값 없음
fallback_model = ""        # 공급자 잔액 부족으로 model이 거절되면 다시 돌릴 모델. 비우면 바꾸지 않는다
executable = ""            # 비우면 기본 설치 경로
account = "u0"
host = "local"
profile = ""
speed = "default"
research_timeout = 1200
reconcile_timeout = 180
capture_timeout = 150
instructions_file = true   # 조사 지시 본문을 파일로 쓰고 세션에는 짧은 지시만 보낸다(host가 local일 때)
autostart = false
app_path = ""

[research]
korean_place_as_korea = true
excluded_domains = ["arxiv.org"]
run_deadline = 3600
out_dir = "pubfinder-runs"
keep_captures = false
```
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from .aside.settings import SettingsError

ASIDE_KEYS = {"model": str, "effort": str, "fallback_model": str, "executable": str, "account": str, "host": str, "profile": str,
              "speed": str, "home": str, "preflight_timeout": float, "research_timeout": float,
              "reconcile_timeout": float, "capture_timeout": float, "stop_timeout": float,
              "launch_interval": float, "instructions_file": bool, "autostart": bool, "app_path": str, "autostart_wait": float}
RESEARCH_KEYS = {"korean_place_as_korea": bool, "excluded_domains": list, "run_deadline": float, "out_dir": str,
                 "keep_captures": bool}


def _typed(section: str, values: dict, schema: dict[str, type]) -> dict[str, Any]:
    unknown = sorted(set(values) - set(schema))
    if unknown:
        raise SettingsError(f"설정 파일 [{section}]에 모르는 키가 있습니다: {', '.join(unknown)}")
    out: dict[str, Any] = {}
    for key, value in values.items():
        kind = schema[key]
        if kind is float and isinstance(value, (int, float)) and not isinstance(value, bool):
            out[key] = float(value)
        elif kind is list and isinstance(value, list) and all(isinstance(item, str) for item in value):
            out[key] = tuple(value)
        elif isinstance(value, kind) and not (kind is not bool and isinstance(value, bool)):
            out[key] = value
        else:
            raise SettingsError(f"설정 파일 [{section}] {key}의 값 꼴이 틀렸습니다")
    return out


def load(path: str | Path | None) -> tuple[dict[str, Any], dict[str, Any]]:
    """(aside 설정, research 설정). 파일이 없으면 둘 다 빈 dict."""
    if not path:
        return {}, {}
    file = Path(path)
    try:
        data = tomllib.loads(file.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SettingsError(f"설정 파일이 없습니다: {file}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise SettingsError(f"설정 파일을 읽지 못했습니다: {exc}") from exc
    unknown = sorted(set(data) - {"aside", "research"})
    if unknown:
        raise SettingsError(f"설정 파일에 모르는 절이 있습니다: {', '.join(unknown)}")
    aside = _typed("aside", data.get("aside") or {}, ASIDE_KEYS)
    if not aside.get("executable"):
        aside.pop("executable", None)
    return aside, _typed("research", data.get("research") or {}, RESEARCH_KEYS)
