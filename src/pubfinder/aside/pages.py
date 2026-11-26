"""다시 연 원문의 상태 판정(차단·빈 화면·이동 중)과 PDF 글 추출."""

from __future__ import annotations

import io
import re

# 차단 화면에 흔한 문구. 본문이 짧을 때만 본다(제목에 CAPTCHA가 든 논문 페이지를 차단으로 보지 않게).
_BLOCK_TEXT = re.compile(
    r"checking your browser|verify (?:that )?you are (?:a )?human|are you a robot|access denied|just a moment"
    r"|unusual traffic|captcha|403 forbidden|429 too many requests|request (?:was )?blocked|attention required"
    r"|접근이 (?:거부|차단)|접근 거부|자동화된 (?:요청|접근|트래픽)|로봇이 아닙니다|사람인지 확인|보안 확인",
    re.IGNORECASE,
)
_BLOCK_TEXT_MAX_CHARS = 3000
# 파일을 받았는데 HTML이 왔을 때 봇 확인·중간 화면인지 보는 표지.
_FILE_CHALLENGE_MARKERS = ("_cf_chl_opt", "cdn-cgi/challenge-platform", "bm-verify", "_sec/verify",
                           "<apm_do_not_touch>", "interstitial")
BLOCKED_HTTP = {401, 403, 429}
MAX_PDF_PAGES = 600


def block_reason(*, http_status: object, visible_text: str, challenge: dict | None) -> str:
    """차단이면 사유, 아니면 빈 문자열. 차단은 기록하고 그 주소는 더 시도하지 않는다."""
    if isinstance(challenge, dict) and challenge.get("seen") and not challenge.get("resolved"):
        return "challenge_unresolved"
    if isinstance(http_status, int) and http_status in BLOCKED_HTTP:
        return f"http_{http_status}"
    text = re.sub(r"\s+", " ", visible_text or "").strip()
    if len(text) <= _BLOCK_TEXT_MAX_CHARS and _BLOCK_TEXT.search(text):
        return "block_page"
    return ""


def page_state(visible_text: str, title: str = "") -> str:
    """fetched·empty·redirect_pending."""
    text = (visible_text or "").strip()
    if not text:
        return "redirect_pending" if re.fullmatch(r"redirecting(?:\.{1,3}|…)?", title.strip(), re.I) else "empty"
    if re.fullmatch(r"redirecting(?:\s*(?:\.{1,3}|…))?", text, re.I):
        return "redirect_pending"
    return "fetched"


def file_challenge(content: bytes) -> bool:
    """파일 주소가 PDF 대신 봇 확인·중간 HTML을 돌려줬는가."""
    if content.startswith(b"%PDF") or not content.lstrip().startswith(b"<"):
        return False
    head = content[:65536].decode("utf-8", "replace").lower()
    return any(marker in head for marker in _FILE_CHALLENGE_MARKERS)


def pdf_text(content: bytes) -> str:
    """쪽 글을 form-feed로 잇는다. 글자 층이 없는 스캔 PDF는 빈 글이 된다(OCR은 하지 않는다)."""
    import logging

    from pypdf import PdfReader

    # 깨진 PDF를 읽을 때 라이브러리가 남기는 경고는 결과 사유로 대신한다.
    logging.getLogger("pypdf").setLevel(logging.ERROR)
    try:
        reader = PdfReader(io.BytesIO(content))
        return "\f".join((page.extract_text() or "") for page in reader.pages[:MAX_PDF_PAGES])
    except Exception as exc:  # noqa: BLE001 - 깨진 PDF는 형식과 무관하게 같은 사유로 남긴다.
        raise ValueError(f"pdf_parse_failed:{type(exc).__name__}") from exc
