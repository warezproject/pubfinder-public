"""문자열 정규화와 지문(sha256). 판정·인용 확인·입력 결속이 함께 쓴다."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata

_HANGUL = re.compile(r"[가-힣]")


def encoded(value: object) -> str:
    """키 순서와 공백을 고정한 JSON 문자열."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def fingerprint(value: object) -> str:
    return hashlib.sha256(encoded(value).encode("utf-8")).hexdigest()


def normalized(value: object) -> str:
    """NFKC, 공백 하나로 접기, 앞뒤 공백 제거, 대소문자 무시. 두 조사 값의 비교에 쓴다."""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", str(value or ""))).strip().casefold()


def text_key(value: object) -> str:
    """숫자·영문·한글만 남긴다. 인용이 다시 연 원문에 있는지 볼 때 쓴다."""
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return re.sub(r"[^0-9a-z가-힣]+", "", text)


def has_hangul(value: object) -> bool:
    return bool(_HANGUL.search(str(value or "")))
