"""근거 주소 검사. 공개 HTTP(S) 주소만, 제외 출처는 근거로 받지 않는다."""

from __future__ import annotations

import ipaddress
import socket
from typing import Any, Callable, Iterable
from urllib.parse import urlsplit

# 사전 공개본은 학술대회 발표 근거가 아니다. 설정으로 바꿀 수 있다.
DEFAULT_EXCLUDED_DOMAINS: tuple[str, ...] = ("arxiv.org",)

Resolver = Callable[..., Any]


def _host(url: str) -> str:
    try:
        parsed = urlsplit(url)
    except ValueError:
        return ""
    if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password:
        return ""
    return (parsed.hostname or "").lower().rstrip(".")


def source_allowed(url: str, excluded: Iterable[str] = DEFAULT_EXCLUDED_DOMAINS) -> bool:
    """조사 보고의 근거 주소로 받을 수 있는가. 이름 해석은 하지 않는다(보고 형식 검사용)."""
    host = _host(url)
    if not host or host == "localhost" or host.endswith(".localhost"):
        return False
    if any(host == domain or host.endswith("." + domain) for domain in (d.lower().strip(".") for d in excluded)):
        return False
    try:
        return ipaddress.ip_address(host).is_global
    except ValueError:
        return True


def public_url(url: str, *, resolve: Resolver = socket.getaddrinfo) -> bool:
    """다시 열기 전에 본다. 이름이 가리키는 주소가 모두 공인 주소여야 한다(내부망 접근 방지)."""
    host = _host(url)
    if not host:
        return False
    try:
        parsed = urlsplit(url)
        rows = resolve(host, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)
        return bool(rows) and all(ipaddress.ip_address(row[4][0]).is_global for row in rows)
    except (ValueError, OSError, IndexError, TypeError):
        return False
