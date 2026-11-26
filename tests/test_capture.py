import base64
import json
import unittest

from pubfinder.aside import pages, transcript
from pubfinder.aside.capture import Capturer, file_script, looks_like_file, page_script, reply_for
from pubfinder.aside.client import AsideClient
from pubfinder.quotes import quote_found
from pubfinder.textutil import text_key
from tests.fakes import scenes

URL = "https://imagconf.example.org/2026/page"


def capturer(settings) -> Capturer:
    return Capturer(AsideClient(settings), resolve=scenes.fake_resolve)


class ScriptTests(unittest.TestCase):
    def test_page_script_clicks_human_verification_once(self):
        script = page_script(URL, "abc123")
        self.assertIn("_cf_chl_opt", script)
        self.assertIn("cpfClickChallenge", script)
        self.assertIn("!cpfChallenge.clicked", script)
        self.assertNotIn("reload", script)

    def test_page_script_reads_child_frames(self):
        # frameset 화면은 바깥 문서에 글이 없다. 첫 프레임(바깥 문서)을 빼고 안쪽 프레임 글·HTML을 덧붙인다.
        script = page_script(URL, "abc123")
        self.assertIn("cpfTab.frames().slice(1, 11)", script)
        self.assertIn("frame_urls", script)
        # Aside REPL에서 맞지 않거나 없는 호출은 쓰지 않는다(2026-11-15 확인).
        self.assertNotIn("mainFrame()", script.split("FRAMES")[0] if "FRAMES" in script else script)
        self.assertNotIn("waitForLoadState", script)

    def test_file_script_sends_no_cookies(self):
        script = file_script(URL, "abc123")
        self.assertIn("credentials: 'omit'", script)
        self.assertNotIn("'include'", script)

    def test_reply_keeps_line_separator_inside_json(self):
        value = {"marker": "m1", "status": "ok", "title": "a b"}
        stdout = "noise\n" + json.dumps(value, ensure_ascii=False) + "\n"
        self.assertEqual(reply_for(stdout, "m1")["title"], "a b")
        self.assertIsNone(reply_for(stdout, "m2"))

    def test_file_like_urls(self):
        self.assertTrue(looks_like_file("https://x.example.org/a/program.PDF"))
        self.assertTrue(looks_like_file("https://x.example.org/article/123/pdf"))
        self.assertFalse(looks_like_file("https://x.example.org/program"))


class BlockRuleTests(unittest.TestCase):
    def test_status_codes_and_challenge(self):
        self.assertEqual(pages.block_reason(http_status=403, visible_text="x", challenge=None), "http_403")
        self.assertEqual(pages.block_reason(http_status=429, visible_text="x", challenge=None), "http_429")
        self.assertEqual(pages.block_reason(http_status=200, visible_text="x",
                                            challenge={"seen": True, "resolved": False}), "challenge_unresolved")
        self.assertEqual(pages.block_reason(http_status=200, visible_text="x",
                                            challenge={"seen": True, "resolved": True}), "")

    def test_short_block_pages_in_two_languages(self):
        self.assertEqual(pages.block_reason(http_status=200, visible_text="Access denied. Ray ID 1",
                                            challenge=None), "block_page")
        self.assertEqual(pages.block_reason(http_status=200, visible_text="자동화된 요청이 감지되어 접근이 차단되었습니다",
                                            challenge=None), "block_page")

    def test_long_paper_page_with_captcha_in_title_is_not_blocked(self):
        text = "Robust CAPTCHA Recognition with Made-up Networks. " + "Session A1 talk details. " * 200
        self.assertEqual(pages.block_reason(http_status=200, visible_text=text, challenge=None), "")

    def test_file_challenge_markers(self):
        self.assertTrue(pages.file_challenge(b"<html><script>window._cf_chl_opt={}</script></html>"))
        self.assertFalse(pages.file_challenge(b"%PDF-1.4 ..."))
        self.assertFalse(pages.file_challenge(b"<html><body>plain program</body></html>"))

    def test_pdf_text(self):
        self.assertIn("Session B2", pages.pdf_text(scenes.tiny_pdf("Session B2: Probing Methods")))
        with self.assertRaises(ValueError):
            pages.pdf_text(b"%PDF-1.4 broken")


class CaptureTests(unittest.TestCase):
    def scene(self, pages_: dict, files: dict | None = None) -> dict:
        scene = scenes.default_scene()
        scene["pages"] = pages_
        scene["files"] = files or {}
        return scene

    def test_fetched_page(self):
        with scenes.fake_aside(self.scene({URL: {"text": "Program text", "html": "<p>Program text</p>"}})) as (s, _, __):
            result = capturer(s).capture(URL)
            self.assertEqual((result["status"], result["kind"]), ("fetched", "page"))
            self.assertEqual(result["visible_text"], "Program text")
            self.assertEqual(len(result["sha256"]), 64)

    def test_frame_urls_are_recorded(self):
        page = {"text": "Program inside a frame", "frame_urls": ["https://imagconf.example.org/2026/inner"]}
        with scenes.fake_aside(self.scene({URL: page})) as (s, _, __):
            result = capturer(s).capture(URL)
            self.assertEqual((result["status"], result["kind"]), ("fetched", "page"))
            self.assertEqual(result["frame_urls"], ["https://imagconf.example.org/2026/inner"])

    def test_forbidden_page_is_blocked_and_body_not_kept(self):
        with scenes.fake_aside(self.scene({URL: {"text": "Forbidden", "http_status": 403}})) as (s, _, __):
            result = capturer(s).capture(URL)
            self.assertEqual((result["status"], result["blocked_reason"]), ("blocked", "http_403"))
            self.assertNotIn("body", result)
            self.assertNotIn("visible_text", result)

    def test_unresolved_challenge_is_blocked(self):
        page = {"text": "Just a moment...", "challenge": {"seen": True, "clicked": "widget_position", "resolved": False}}
        with scenes.fake_aside(self.scene({URL: page})) as (s, _, __):
            result = capturer(s).capture(URL)
            self.assertEqual(result["blocked_reason"], "challenge_unresolved")
            self.assertEqual(result["challenge"]["clicked"], "widget_position")

    def test_empty_page_is_fetched_again_as_file(self):
        pdf = scenes.pdf_entry("Program in a file")
        with scenes.fake_aside(self.scene({URL: {"text": ""}}, {URL: pdf})) as (s, _, __):
            result = capturer(s).capture(URL)
            self.assertEqual((result["status"], result["kind"]), ("fetched", "file"))
            self.assertEqual(result["page_attempt"]["status"], "empty")
            self.assertIn("Program in a file", result["text"])

    def test_file_challenge_is_blocked(self):
        url = URL + ".pdf"
        html = base64.b64encode(b"<html><script>_cf_chl_opt</script></html>").decode()
        files = {url: {"b64": html, "content_type": "text/html", "http_status": 200}}
        with scenes.fake_aside(self.scene({}, files)) as (s, _, __):
            result = capturer(s).capture(url)
            self.assertEqual((result["status"], result["blocked_reason"]), ("blocked", "challenge_on_file"))

    def test_internal_addresses_are_not_opened(self):
        with scenes.fake_aside(self.scene({})) as (s, _, calls):
            result = capturer(s).capture("https://intranet.internal.example/program")
            self.assertEqual(result["status"], "unsafe")
            self.assertEqual([call for call in calls() if call[:1] == ["repl"]], [])
        redirect = {URL: {"text": "x", "final_url": "https://intranet.internal.example/x"}}
        with scenes.fake_aside(self.scene(redirect)) as (s, _, __):
            self.assertEqual(capturer(s).capture(URL)["status"], "unsafe")

    def test_missing_page_reply(self):
        with scenes.fake_aside(self.scene({})) as (s, _, __):
            result = capturer(s).page(URL)
            self.assertEqual((result["status"], result["error_type"]), ("access_failed", "navigation_failed"))


class TranscriptTests(unittest.TestCase):
    def test_error_kinds(self):
        self.assertEqual(transcript.error_kind(["Rate limit reached, 429"]), "usage_limit")
        self.assertEqual(transcript.error_kind(["Requested model is not available for this account"]), "auth")
        self.assertEqual(transcript.error_kind(["something else"]), "provider_error")
        self.assertEqual(transcript.error_kind([]), "")

    def test_out_of_credit(self):
        real = ('400 {"type":"error","error":{"type":"invalid_request_error","message":"Your credit balance is too '
                'low to access the Anthropic API. Please go to Plans & Billing to upgrade or purchase credits."},'
                '"request_id":"req_x"}')
        summary = {"errors": [real], "final_text": "", "models": ["m"], "model_verification": "matched"}
        self.assertTrue(transcript.out_of_credit(summary))
        self.assertEqual(transcript.error_kind([real]), "usage_limit")
        self.assertEqual(transcript.failure_status({"termination": "normal"}, summary),
                         "research_unavailable:out_of_credit")
        self.assertTrue(transcript.provider_message(real).startswith("Your credit balance is too low"))
        self.assertEqual(transcript.provider_message("plain words"), "plain words")
        # 속도 한도는 기다리면 풀린다. 잔액 부족으로 보지 않는다.
        self.assertFalse(transcript.out_of_credit({"errors": ["429 rate limit reached"], "final_text": ""}))
        self.assertFalse(transcript.out_of_credit({"errors": [], "final_text": ""}))

    def test_provider_refusal_picks_only_limits_credit_and_connection(self):
        def refusal(error, final_text=""):
            return transcript.provider_refusal({"errors": [error], "final_text": final_text})

        self.assertEqual(refusal("Your credit balance is too low to access the Anthropic API"), "out_of_credit")
        self.assertEqual(refusal("429 rate limit reached"), "usage_limit")
        self.assertEqual(refusal("Requested model is not available for this account"), "auth")
        self.assertEqual(refusal("404 model not found"), "")
        # 모델이 끝 답을 냈으면 거절이 아니다.
        self.assertEqual(refusal("429 rate limit reached", final_text="RESEARCH_DONE x"), "")
        self.assertEqual(transcript.provider_refusal({"errors": [], "final_text": ""}), "")

    def test_start_failure_is_not_a_research_attempt(self):
        summary = transcript.adopt_exec_errors(transcript.summarize(None), " • Error credentials.json locked\n")
        self.assertEqual(summary["error_kind"], "start_failed")
        self.assertEqual(transcript.failure_status({"termination": "normal"}, summary), "research_not_started")

    def test_model_verification(self):
        self.assertEqual(transcript.model_verification({"models": ["m-1"]}, "p/m-1"), "matched")
        self.assertEqual(transcript.model_verification({"models": ["m-2"]}, "p/m-1"), "mismatch")
        self.assertEqual(transcript.model_verification({"models": []}, "p/m-1"), "unavailable")


class QuoteTests(unittest.TestCase):
    def test_segments_need_length(self):
        page = text_key("Session B2: Probing Methods, Wednesday 10 June 2026, 14:00-15:30")
        self.assertTrue(quote_found("Session B2: Probing Methods … 10 June 2026", page))
        self.assertFalse(quote_found("June … 2026", page))
        self.assertFalse(quote_found("Session B2: Probing Methods … not on the page", page))


if __name__ == "__main__":
    unittest.main()
