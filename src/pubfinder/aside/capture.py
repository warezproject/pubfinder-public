"""근거 다시 열기. 두 조사가 적은 주소를 프로그램이 정해 둔 `aside repl` 스크립트로 Aside 브라우저에서 다시 연다.

- 화면 근거: 새 탭으로 열고 원문 HTML과 화면 글을 받는다.
  사람 확인 화면(Cloudflare 확인 화면 구조 표지 `_cf_chl_opt` 또는 확인 주소)이 보이면 최대 45초 기다리며
  확인 체크박스를 한 번 누른다(2026-11-15 사용자 결정: 항상 켬). 그래도 남으면 차단으로 기록하고 멈춘다.
- 파일 근거(PDF 등): 빈 탭을 하나 띄운 상태에서 REPL의 fetch로 받는다. 로그인 쿠키는 싣지 않는다
  (`credentials: 'omit'`, 2026-11-15 결정). PDF 대신 확인 화면이 오면 차단으로 기록한다.
- 401·403·429와 짧은 차단 문구 화면은 차단이다. 차단 뒤 다른 방법으로 다시 받지 않는다.
- 본문은 base64로 표준출력 JSON에 싣는다(세션 폴더 파일에 기대지 않는다).
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import secrets
import time
from typing import Any, Callable
from urllib.parse import urlsplit

from ..sources import public_url
from . import pages
from .client import AsideClient

MAX_CAPTURE_BYTES = 30 * 1024 * 1024
CHALLENGE_WAIT_SECONDS = 45
LOAD_WAIT_SECONDS = 10
MAX_CAPTURE_URLS = 20


def _inline_js() -> str:
    return (
        f"const cpfInlineMax = {MAX_CAPTURE_BYTES};\n"
        "const cpfInline = (key, data) => {\n"
        "  const buf = Buffer.isBuffer(data) ? data\n"
        "    : (data instanceof Uint8Array ? Buffer.from(data) : Buffer.from(String(data ?? ''), 'utf8'));\n"
        "  return buf.length > cpfInlineMax ? {[key + '_omitted']: 'too_large'} : {[key]: buf.toString('base64')};\n"
        "};\n"
    )


# 사람 확인 체크박스를 한 번 누른다. 확인 프레임 안 체크박스가 먼저이고, 못 찾으면 위젯 위치를 누른다.
CHALLENGE_CLICK_JS = r"""
  const cpfClickChallenge = async (tab) => {
    try {
      for (const frame of tab.frames()) {
        if (!String(frame.url() || '').includes('challenges.cloudflare.com')) continue;
        const box = frame.locator('input[type="checkbox"]').first();
        if (await box.count()) { await box.click({timeout: 3000}); return 'frame_checkbox'; }
      }
    } catch (cpfIgnored) {}
    try {
      const rect = await tab.evaluate(() => {
        const node = document.querySelector(
          'iframe[src*="challenges.cloudflare.com"], .cf-turnstile, [id^="cf-chl-widget"]');
        if (!node) return null;
        const box = node.getBoundingClientRect();
        return box.width > 0 && box.height > 0 ? {x: box.x, y: box.y, w: box.width, h: box.height} : null;
      });
      if (rect) {
        await tab.mouse.click(rect.x + Math.min(30, rect.w / 2), rect.y + rect.h / 2);
        return 'widget_position';
      }
    } catch (cpfIgnored) {}
    return '';
  };
"""


def wait_script(tab: str) -> str:
    """확인 화면이 사라지거나 대기 상한이 끝날 때까지 본다. 다시 불러오기(reload)는 하지 않는다."""
    return (
        f"  const cpfLoadDeadline = Date.now() + {LOAD_WAIT_SECONDS * 1000};\n"
        f"  const cpfChallengeDeadline = Date.now() + {CHALLENGE_WAIT_SECONDS * 1000};\n"
        "  const cpfChallenge = {seen: false, clicked: '', resolved: true};\n"
        + CHALLENGE_CLICK_JS
        + "  while (true) {\n"
        "    let cpfState = {challenge: false, failed: true};\n"
        "    try {\n"
        f"      cpfState = await {tab}.evaluate(() => {{\n"
        "        const raw = document.documentElement ? document.documentElement.outerHTML : '';\n"
        "        const onChallengeUrl = location.href.includes('/cdn-cgi/challenge-platform/');\n"
        "        return {challenge: raw.includes('_cf_chl_opt') || onChallengeUrl, failed: false};\n"
        "      });\n"
        "    } catch (cpfIgnored) {}\n"
        "    if (cpfState.challenge) { cpfChallenge.seen = true; cpfChallenge.resolved = false; }\n"
        "    else if (!cpfState.failed) { cpfChallenge.resolved = true; }\n"
        "    if (!cpfState.failed && !cpfState.challenge) break;\n"
        "    if (Date.now() >= (cpfState.challenge ? cpfChallengeDeadline : cpfLoadDeadline)) break;\n"
        f"    if (cpfState.challenge && !cpfChallenge.clicked) cpfChallenge.clicked = await cpfClickChallenge({tab});\n"
        "    await sleep(2000);\n"
        "  }\n"
    )


# 프레임(frameset·iframe)으로 본문을 띄우는 화면은 바깥 문서에 글이 없다. 안쪽 프레임의 글과 HTML을 덧붙인다
# (2026-11-15 실제 측정에서 학회 안내 화면이 frameset이라 화면 글이 비었다). 첫 프레임은 바깥 문서다. Aside REPL에서는
# mainFrame() 비교가 맞지 않고 프레임에 waitForLoadState가 없어(2026-11-15 확인) 순서로 빼고 잠깐 기다린다.
FRAMES_JS = r"""
  const cpfFrames = [];
  const cpfFrameList = cpfTab.frames().slice(1, 11);
  if (cpfFrameList.length) await sleep(1500);
  for (const frame of cpfFrameList) {
    const frameUrl = String(frame.url() || '');
    if (!/^https?:/i.test(frameUrl)) continue;
    let text = '';
    let html = '';
    try { text = await frame.evaluate(() => document.body ? document.body.innerText : ''); } catch (cpfIgnored) {}
    try { html = await frame.content(); } catch (cpfIgnored) {}
    if (text || html) cpfFrames.push({url: frameUrl, text, html});
  }
"""


def page_script(url: str, marker: str) -> str:
    return (
        f"// CPF_CAPTURE kind=page marker={marker}\n"
        f"const cpfMarker = {json.dumps(marker)};\n"
        f"const cpfUrl = {json.dumps(url)};\n"
        + _inline_js()
        + "const cpfTab = await openTab(cpfUrl);\n"
        "try {\n"
        + wait_script("cpfTab")
        + FRAMES_JS
        + "  const cpfHtml = [await cpfTab.content(), ...cpfFrames.map((f) => f.html)].join('\\n');\n"
        "  const cpfText = [await cpfTab.evaluate(() => document.body ? document.body.innerText : ''),\n"
        "    ...cpfFrames.map((f) => f.text)].filter((t) => t && t.trim()).join('\\n');\n"
        "  const cpfHttp = await cpfTab.evaluate(() => {\n"
        "    try { const entry = performance.getEntriesByType('navigation')[0];\n"
        "          return entry && entry.responseStatus ? entry.responseStatus : 0; } catch (cpfIgnored) { return 0; }\n"
        "  });\n"
        "  console.log(JSON.stringify({marker: cpfMarker, status: 'ok', final_url: cpfTab.url(),\n"
        "    title: await cpfTab.title(), http_status: cpfHttp, challenge: cpfChallenge,\n"
        "    frame_urls: cpfFrames.map((f) => f.url.slice(0, 300)),\n"
        "    ...cpfInline('html_b64', cpfHtml), ...cpfInline('text_b64', cpfText)}));\n"
        "} finally { await closeTab(cpfTab); }\n"
    )


def file_script(url: str, marker: str) -> str:
    # 탭 없는 REPL의 fetch는 브라우저 상태에 따라 멈춘 기록이 있어 빈 탭을 하나 띄운 채 받는다.
    return (
        f"// CPF_CAPTURE kind=file marker={marker}\n"
        f"const cpfMarker = {json.dumps(marker)};\n"
        f"const cpfUrl = {json.dumps(url)};\n"
        + _inline_js()
        + "const cpfTab = await openTab('about:blank');\n"
        "try {\n"
        "  const cpfResponse = await fetch(cpfUrl, {credentials: 'omit', redirect: 'follow'});\n"
        "  const cpfBytes = Buffer.from(await cpfResponse.arrayBuffer());\n"
        "  console.log(JSON.stringify({marker: cpfMarker, status: 'ok', final_url: cpfResponse.url || cpfUrl,\n"
        "    http_status: cpfResponse.status, content_type: cpfResponse.headers.get('content-type') || '',\n"
        "    ...cpfInline('file_b64', cpfBytes)}));\n"
        "} finally { await closeTab(cpfTab); }\n"
    )


def reply_for(stdout: str, marker: str) -> dict[str, Any] | None:
    """REPL 표준출력에서 이 캡처의 JSON 한 줄. 줄바꿈(LF)으로만 나눈다(JSON 안의 U+2028 등 보존)."""
    for line in (stdout or "").split("\n"):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and value.get("marker") == marker:
            return value
    return None


def _body(reply: dict, key: str) -> tuple[bytes | None, str]:
    if reply.get(f"{key}_omitted") == "too_large":
        return None, "too_large"
    raw = reply.get(key)
    if not isinstance(raw, str):
        return None, "body_missing"
    try:
        data = base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError):
        return None, "body_invalid"
    return (None, "too_large") if len(data) > MAX_CAPTURE_BYTES else (data, "")


def _challenge(reply: dict) -> dict:
    value = reply.get("challenge")
    if not isinstance(value, dict) or not value.get("seen"):
        return {}
    return {"seen": True, "clicked": str(value.get("clicked") or "")[:40], "resolved": bool(value.get("resolved"))}


def looks_like_file(url: str) -> bool:
    path = urlsplit(url).path.lower()
    return path.endswith((".pdf", "/pdf")) or "/pdf/" in path or "pdfdirect" in path


class Capturer:
    def __init__(self, client: AsideClient, *, resolve: Callable[..., Any] | None = None) -> None:
        self.client = client
        self.resolve = resolve

    def _public(self, url: str) -> bool:
        return public_url(url, resolve=self.resolve) if self.resolve is not None else public_url(url)

    def _base(self, url: str, kind: str) -> dict[str, Any]:
        return {"url": url, "kind": kind, "status": "unsafe", "final_url": url, "collected_at": time.time()}

    def _reply(self, script: str, marker: str) -> tuple[dict | None, str]:
        result = self.client.repl(script)
        reply = reply_for(result["stdout"], marker)
        if reply is None:
            return None, result["error"] or "aside_repl_failed"
        if reply.get("status") != "ok":
            return None, str(reply.get("status") or "aside_repl_failed")[:80]
        return reply, ""

    def page(self, url: str) -> dict[str, Any]:
        base = self._base(url, "page")
        if not self._public(url):
            return base
        marker = secrets.token_hex(8)
        reply, error = self._reply(page_script(url, marker), marker)
        if reply is None:
            return {**base, "status": "access_failed", "error_type": error}
        final_url = str(reply.get("final_url") or url)
        if not self._public(final_url):
            return {**base, "final_url": final_url}
        html, html_error = _body(reply, "html_b64")
        text, text_error = _body(reply, "text_b64")
        if html is None or text is None:
            return {**base, "status": "too_large" if "too_large" in (html_error, text_error) else "access_failed",
                    "final_url": final_url, "error_type": html_error or text_error}
        visible = text.decode("utf-8", errors="replace")
        http_status = reply.get("http_status") if isinstance(reply.get("http_status"), int) else None
        challenge = _challenge(reply)
        frames = [str(item)[:300] for item in reply.get("frame_urls") or [] if isinstance(item, str)][:10]
        out = {**base, "final_url": final_url, "http_status": http_status or None, "content_type": "text/html",
               "title": str(reply.get("title") or "")[:500], **({"challenge": challenge} if challenge else {}),
               **({"frame_urls": frames} if frames else {})}
        blocked = pages.block_reason(http_status=http_status, visible_text=visible, challenge=challenge)
        if blocked:
            # 차단 화면 사본은 남기지 않는다(요청자 주소 등이 실릴 수 있다).
            return {**out, "status": "blocked", "blocked_reason": blocked}
        if http_status and http_status >= 400:
            return {**out, "status": "access_failed", "error_type": f"http_{http_status}"}
        state = pages.page_state(visible, out["title"])
        if state != "fetched":
            return {**out, "status": state}
        html_text = html.decode("utf-8", errors="replace")
        return {**out, "status": "fetched", "sha256": hashlib.sha256(html).hexdigest(), "size": len(html),
                "visible_text": visible, "text": html_text, "body": html}

    def file(self, url: str) -> dict[str, Any]:
        base = self._base(url, "file")
        if not self._public(url):
            return base
        marker = secrets.token_hex(8)
        reply, error = self._reply(file_script(url, marker), marker)
        if reply is None:
            return {**base, "status": "access_failed", "error_type": error}
        final_url = str(reply.get("final_url") or url)
        if not self._public(final_url):
            return {**base, "final_url": final_url}
        content, body_error = _body(reply, "file_b64")
        http_status = reply.get("http_status") if isinstance(reply.get("http_status"), int) else None
        out = {**base, "final_url": final_url, "http_status": http_status,
               "content_type": str(reply.get("content_type") or "")[:200]}
        if content is None:
            return {**out, "status": "too_large" if body_error == "too_large" else "access_failed",
                    "error_type": body_error}
        if http_status in pages.BLOCKED_HTTP:
            return {**out, "status": "blocked", "blocked_reason": f"http_{http_status}"}
        if pages.file_challenge(content):
            return {**out, "status": "blocked", "blocked_reason": "challenge_on_file"}
        if not isinstance(http_status, int) or not 200 <= http_status < 300 or not content:
            return {**out, "status": "access_failed", "error_type": f"http_{http_status}"}
        is_pdf = content.startswith(b"%PDF")
        try:
            text = pages.pdf_text(content) if is_pdf else content.decode("utf-8", errors="replace")
        except ValueError as exc:
            return {**out, "status": "parse_failed", "error_type": str(exc)}
        if not is_pdf:
            blocked = pages.block_reason(http_status=http_status, visible_text=text, challenge=None)
            if blocked:
                return {**out, "status": "blocked", "blocked_reason": blocked}
        return {**out, "status": "fetched", "content_type": "application/pdf" if is_pdf else out["content_type"],
                "sha256": hashlib.sha256(content).hexdigest(), "size": len(content), "visible_text": "",
                "text": text, "body": content}

    def capture(self, url: str) -> dict[str, Any]:
        """주소 모양으로 파일이면 파일로, 아니면 화면으로 받는다. 화면이 비었거나(확장자 없는 PDF) REPL이 표지 없이
        끝나면(탭이 내려받기로 바뀜) 같은 주소를 파일로 한 번 더 받는다."""
        if looks_like_file(url):
            return self.file(url)
        result = self.page(url)
        if result.get("status") == "empty" or (result.get("status") == "access_failed"
                                               and result.get("error_type") == "aside_repl_failed"):
            retried = self.file(url)
            retried["page_attempt"] = {key: result.get(key) for key in ("status", "error_type")}
            return retried
        return result

    def capture_all(self, urls: list[str], *, deadline: float | None = None,
                    clock: Callable[[], float] = time.monotonic) -> dict[str, dict]:
        captures: dict[str, dict] = {}
        for url in list(dict.fromkeys(url for url in urls if url))[:MAX_CAPTURE_URLS]:
            if deadline is not None and clock() >= deadline:
                captures[url] = {"url": url, "status": "deadline_exceeded"}
                continue
            captures[url] = self.capture(url)
        return captures
