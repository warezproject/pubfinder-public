"""가짜 Aside CLI를 실제 하위 프로세스로 부르는 한 건 실행 시험."""

import copy
import json
import os
import threading
import time
import unittest
from dataclasses import replace
from pathlib import Path

from pubfinder.cli import main
from pubfinder.prompts import HUMAN_VERIFICATION_RULES
from pubfinder.runner import Run, RunOptions
from pubfinder.verdict import RECONCILE_POLICY, STRING_POLICY, verify_result
from tests import helpers as h
from tests.fakes import scenes


def run(settings, root: Path, query=None, **options):
    opts = RunOptions(out_dir=root / "runs", **options)
    return Run(query or h.make_test_query(), settings, opts, resolve=scenes.fake_resolve).execute()


def exec_calls(calls):
    return [call for call in calls() if call[:1] == ["exec"]]


class HappyPathTests(unittest.TestCase):
    def test_full_run_adopts_and_records(self):
        with scenes.fake_aside(scenes.default_scene()) as (settings, root, calls):
            result = run(settings, root)
            self.assertEqual(result["status"], "verified", result.get("warnings"))
            self.assertEqual(result["summary"]["policy"], RECONCILE_POLICY)
            self.assertEqual(result["summary"]["adopted_count"], 12)
            self.assertEqual(result["fields"]["host_country"]["country_code"], "KR")
            self.assertTrue(result["fields"]["presentation_date"]["evidence"]["quote_verified"])
            self.assertTrue(verify_result(result))
            folder = Path(result["run"]["out_dir"])
            for name in ("request.json", "preflight.json", "reconcile.json", "result.json", "captures/summary.json",
                         "exec-primary/prompt.txt", "exec-review/session/messages.jsonl",
                         "exec-primary/session/result.json"):
                self.assertTrue((folder / name).is_file(), name)
            self.assertFalse(list((folder / "captures").glob("*.html")))
            # 조사 세션에는 짧은 지시만 가고, 본문(공통 지시 포함)은 실행 기록 폴더의 지시 파일에 있다.
            for role in ("primary", "review"):
                sent = (folder / f"exec-{role}" / "prompt.txt").read_text(encoding="utf-8")
                body = (folder / f"exec-{role}" / "instructions.txt").read_text(encoding="utf-8")
                self.assertLess(len(sent), 1500)
                self.assertIn((folder / f"exec-{role}" / "instructions.txt").resolve().as_posix(), sent)
                self.assertIn(h.TITLE, sent)
                self.assertNotIn("[보고서 관찰 규칙]", sent)
                self.assertTrue(body.startswith(HUMAN_VERIFICATION_RULES))
                self.assertIn(f"./artifacts/cpf/{result['run']['run_id']}/{role}/result.json", body)
            self.assertNotIn(h.make_test_query().fingerprint(),
                             (folder / "exec-review" / "prompt.txt").read_text(encoding="utf-8"))
            reconcile_prompt = (folder / "exec-reconcile" / "prompt.txt").read_text(encoding="utf-8")
            self.assertTrue(reconcile_prompt.startswith(HUMAN_VERIFICATION_RULES))
            execs = exec_calls(calls)
            self.assertEqual(len(execs), 3)
            for call in execs:
                self.assertEqual(call[call.index("--model") + 1], "fake-provider/fake-model")
                self.assertEqual(call[call.index("--effort") + 1], "medium")
                self.assertEqual(call[call.index("--profile") + 1], "00000000-0000-0000-0000-000000000001")
            self.assertEqual(result["run"]["sessions"]["primary"]["model_verification"], "matched")
            self.assertNotEqual(result["run"]["sessions"]["primary"]["session_id"],
                                result["run"]["sessions"]["review"]["session_id"])

    def test_instructions_file_off_sends_whole_prompt(self):
        with scenes.fake_aside(scenes.default_scene()) as (settings, root, _):
            result = run(replace(settings, instructions_file=False), root)
            self.assertEqual(result["status"], "verified", result.get("warnings"))
            folder = Path(result["run"]["out_dir"]) / "exec-primary"
            self.assertFalse((folder / "instructions.txt").exists())
            self.assertTrue((folder / "prompt.txt").read_text(encoding="utf-8").startswith(HUMAN_VERIFICATION_RULES))

    def test_remote_host_gets_whole_prompt(self):
        # 다른 PC의 세션은 이 PC의 지시 파일을 읽을 수 없다.
        with scenes.fake_aside(scenes.default_scene()) as (settings, root, _):
            result = run(replace(settings, host="remote-box"), root)
            folder = Path(result["run"]["out_dir"]) / "exec-primary"
            self.assertFalse((folder / "instructions.txt").exists())
            self.assertTrue((folder / "prompt.txt").read_text(encoding="utf-8").startswith(HUMAN_VERIFICATION_RULES))

    def test_unreadable_instructions_file_is_reported(self):
        scene = scenes.default_scene()
        scene["instructions_unreadable"] = True
        with scenes.fake_aside(scene) as (settings, root, _):
            result = run(settings, root)
            self.assertEqual(result["run"]["sessions"]["primary"]["failure"], "instructions_unreadable")

    def test_keep_captures_writes_fetched_bodies_only(self):
        scene = scenes.default_scene()
        scene["pages"][h.EVENT_URL]["challenge"] = {"seen": True, "clicked": "frame_checkbox", "resolved": False}
        with scenes.fake_aside(scene) as (settings, root, _):
            result = run(settings, root, keep_captures=True)
            folder = Path(result["run"]["out_dir"]) / "captures"
            self.assertEqual(len(list(folder.glob("*.html"))), 1)
            blocked = [item for item in result["captures"] if item["status"] == "blocked"]
            self.assertEqual([item["blocked_reason"] for item in blocked], ["challenge_unresolved"])
            self.assertEqual(result["fields"]["host_country"]["reason"], "capture_unverified")
            self.assertIn("venue_name", result["summary"]["adopted"])
            self.assertTrue(any("차단" in note for note in result["warnings"]))

    def test_pdf_program_quote_is_verified_with_pdf_text(self):
        pdf_url = "https://imagconf.example.org/2026/program.pdf"
        obs = h.observations()
        obs["fields"]["session"]["evidence"] = [h.ev(pdf_url, "Session B2: Probing Methods", "official_program")]
        scene = scenes.default_scene()
        scene["exec"]["primary"]["result"] = scenes.primary_result(obs)
        scene["exec"]["review"]["result"] = scenes.review_result(obs)
        scene["files"][pdf_url] = scenes.pdf_entry("Session B2: Probing Methods, Room 3")
        with scenes.fake_aside(scene) as (settings, root, _):
            result = run(settings, root)
            item = next(item for item in result["captures"] if item["url"] == pdf_url)
            self.assertEqual((item["status"], item["kind"], item["verified_quote_count"]), ("fetched", "file", 1))
            self.assertTrue(result["fields"]["session"]["evidence"]["quote_verified"])


class RetryAndFallbackTests(unittest.TestCase):
    def test_review_without_evidence_is_retried_once(self):
        empty = {"identity": h.unknown(), "event": h.unknown(), "fields": {}, "limitations": []}
        scene = scenes.default_scene()
        scene["exec"]["review"]["result"] = scenes.review_result(empty)
        scene["exec"]["review-retry"] = {"result": scenes.review_result(h.observations())}
        with scenes.fake_aside(scene) as (settings, root, calls):
            result = run(settings, root)
            self.assertEqual(result["run"]["review_retry"]["status"], "completed")
            self.assertTrue(result["run"]["review_retry"]["has_evidence"])
            self.assertEqual(result["status"], "verified")
            self.assertEqual(len(exec_calls(calls)), 4)
            self.assertIn("review-retry", result["run"]["sessions"])

    def test_bad_reconcile_answer_falls_back_to_string_policy(self):
        scene = scenes.default_scene()
        scene["exec"]["reconcile"]["final_text"] = "대조할 수 없습니다."
        with scenes.fake_aside(scene) as (settings, root, _):
            result = run(settings, root)
            self.assertEqual(result["summary"]["policy"], STRING_POLICY)
            self.assertEqual(result["status"], "verified")
            self.assertTrue(any("문자열 일치" in note for note in result["warnings"]))

    def test_reconcile_answer_is_read_from_stdout_when_session_record_is_gone(self):
        scene = scenes.default_scene()
        scene["exec"]["reconcile"]["purge_session_folder"] = True
        with scenes.fake_aside(scene) as (settings, root, _):
            result = run(settings, root)
            self.assertEqual(result["summary"]["policy"], RECONCILE_POLICY)
            self.assertEqual(result["run"]["reconcile_status"], "parsed")
            self.assertEqual(result["run"]["sessions"]["reconcile"]["failure"], "")

    def test_without_year_and_reconcile_nothing_is_adopted(self):
        scene = scenes.default_scene()
        scene["exec"]["reconcile"]["final_text"] = "no json"
        with scenes.fake_aside(scene) as (settings, root, _):
            result = run(settings, root, query=h.make_test_query(year="", venue="IMAGCONF"))
            self.assertEqual(result["status"], "not_verified")
            self.assertTrue(any("--year" in note for note in result["warnings"]))


class FailureTests(unittest.TestCase):
    def test_not_signed_in(self):
        scene = scenes.default_scene()
        scene["signed_in"] = False
        with scenes.fake_aside(scene) as (settings, root, calls):
            result = run(settings, root)
            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["reason"], "aside_unavailable:aside_account_not_signed_in")
            self.assertEqual(exec_calls(calls), [])

    def test_app_not_running(self):
        scene = scenes.default_scene()
        scene["app_running"] = False
        with scenes.fake_aside(scene) as (settings, root, _):
            result = run(settings, root)
            self.assertTrue(result["reason"].startswith("aside_unavailable:aside_not_running"))

    def test_timeout_stops_the_session(self):
        scene = scenes.default_scene()
        scene["exec"]["primary"]["sleep"] = 30
        with scenes.fake_aside(scene) as (settings, root, calls):
            result = run(replace(settings, research_timeout=3.0), root)
            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["reason"], "primary:incomplete")
            stops = [call for call in calls() if call[:2] == ["session", "stop"]]
            self.assertEqual([call[-1] for call in stops], [result["run"]["sessions"]["primary"]["session_id"]])

    def test_cancel_stops_running_sessions(self):
        scene = scenes.default_scene()
        scene["exec"]["primary"]["sleep"] = 30
        scene["exec"]["review"]["sleep"] = 30
        with scenes.fake_aside(scene) as (settings, root, calls):
            runner = Run(h.make_test_query(), settings, RunOptions(out_dir=root / "runs"), resolve=scenes.fake_resolve)
            threading.Timer(3.0, runner.cancel.set).start()
            started = time.monotonic()
            result = runner.execute()
            self.assertLess(time.monotonic() - started, 20)
            self.assertEqual(result["reason"], "primary:incomplete")
            stopped = {call[-1] for call in calls() if call[:2] == ["session", "stop"]}
            self.assertEqual(stopped, {result["run"]["sessions"][role]["session_id"] for role in ("primary", "review")})

    def test_too_long_command_is_not_launched(self):
        with scenes.fake_aside(scenes.default_scene()) as (settings, root, calls):
            query = h.make_test_query(title="가" * 1990, authors=["나" * 290] * 100)
            result = run(settings, root, query=query)
            if os.name == "nt":
                self.assertEqual(result["reason"], "primary:research_unavailable")
                self.assertEqual(exec_calls(calls), [])

    def test_session_that_never_started(self):
        scene = scenes.default_scene()
        scene["exec"]["primary"] = {"no_session": True, "stdout_error": "credentials.json is locked"}
        with scenes.fake_aside(scene) as (settings, root, _):
            result = run(settings, root)
            self.assertEqual(result["reason"], "primary:research_not_started")

    def test_different_model_is_not_trusted(self):
        scene = scenes.default_scene()
        scene["exec"]["primary"]["model_used"] = "other-provider/other-model"
        with scenes.fake_aside(scene) as (settings, root, _):
            self.assertEqual(run(settings, root)["reason"], "primary:research_unavailable")

    def test_usage_limit(self):
        scene = scenes.default_scene()
        scene["exec"]["primary"] = {"errors": ["429 You have hit your usage limit"], "work": False,
                                    "final_text": ""}
        with scenes.fake_aside(scene) as (settings, root, _):
            self.assertEqual(run(settings, root)["reason"], "primary:research_unavailable")

    def test_out_of_credit_switches_to_fallback_model(self):
        scene = scenes.default_scene()
        scene["out_of_credit_models"] = ["fake-provider/fake-model"]
        with scenes.fake_aside(scene) as (settings, root, calls):
            settings = replace(settings, fallback_model="backup-provider/backup-model")
            result = run(settings, root)
            self.assertEqual(result["status"], "verified", result.get("reason"))
            models = [call[call.index("--model") + 1] for call in exec_calls(calls)]
            # 1차·2차가 함께 거절되고 둘 다 대체 모델로 다시 돈다. 대조는 처음부터 대체 모델로 돈다.
            self.assertEqual(sorted(models), ["backup-provider/backup-model"] * 3 + ["fake-provider/fake-model"] * 2)
            fallback = result["run"]["model_fallback"]
            self.assertEqual((fallback["from"], fallback["to"]),
                             ("fake-provider/fake-model", "backup-provider/backup-model"))
            self.assertIn("credit balance is too low", fallback["message"])
            primary = result["run"]["sessions"]["primary"]
            self.assertEqual(primary["model"], "backup-provider/backup-model")
            self.assertEqual(primary["model_verification"], "matched")
            self.assertEqual(primary["fallback_from"]["model"], "fake-provider/fake-model")
            self.assertTrue(any("대체 모델 backup-provider/backup-model" in note for note in result["warnings"]))
            folder = Path(result["run"]["out_dir"])
            self.assertTrue((folder / "exec-primary" / "fallback" / "session" / "result.json").is_file())

    def test_out_of_credit_without_fallback_says_so(self):
        scene = scenes.default_scene()
        scene["out_of_credit_models"] = ["fake-provider/fake-model"]
        with scenes.fake_aside(scene) as (settings, root, calls):
            result = run(settings, root)
            self.assertEqual(result["reason"], "primary:research_unavailable:out_of_credit")
            self.assertEqual(len(exec_calls(calls)), 2)
            self.assertIsNone(result["run"]["model_fallback"])

    def test_fallback_model_out_of_credit_too_is_not_retried_again(self):
        scene = scenes.default_scene()
        scene["out_of_credit_models"] = ["fake-provider/fake-model", "backup-provider/backup-model"]
        with scenes.fake_aside(scene) as (settings, root, calls):
            result = run(replace(settings, fallback_model="backup-provider/backup-model"), root)
            self.assertEqual(result["reason"], "primary:research_unavailable:out_of_credit")
            self.assertEqual(len(exec_calls(calls)), 4)

    def test_usage_limit_switches_to_fallback_model(self):
        scene = scenes.default_scene()
        # 1차만 한도에 걸린다. 대체 모델로 다시 돈 1차는 장면의 정상 결과를 낸다.
        scene["exec"]["primary"]["errors_for_models"] = {
            "fake-provider/fake-model": ['429 {"type":"error","error":{"type":"rate_limit_error",'
                                         '"message":"You have hit your usage limit"}}']}
        with scenes.fake_aside(scene) as (settings, root, calls):
            result = run(replace(settings, fallback_model="backup-provider/backup-model"), root)
            self.assertEqual(result["status"], "verified", result.get("reason"))
            self.assertEqual(result["run"]["model_fallback"]["reason"], "usage_limit")
            self.assertEqual(result["run"]["model_fallback"]["message"], "You have hit your usage limit")
            self.assertTrue(any("사용량 한도" in note for note in result["warnings"]))
            # 2차는 이미 주 모델로 끝났다. 대조는 바꾼 뒤라 대체 모델로 돈다.
            self.assertEqual(result["run"]["sessions"]["review"]["model"], "fake-provider/fake-model")
            self.assertEqual(result["run"]["sessions"]["reconcile"]["model"], "backup-provider/backup-model")

    def test_unavailable_model_reported_on_stderr_switches_to_fallback(self):
        scene = scenes.default_scene()
        scene["unavailable_models"] = ["fake-provider/fake-model"]
        with scenes.fake_aside(scene) as (settings, root, _):
            result = run(replace(settings, fallback_model="backup-provider/backup-model"), root)
            self.assertEqual(result["status"], "verified", result.get("reason"))
            fallback = result["run"]["model_fallback"]
            self.assertEqual(fallback["reason"], "auth")
            self.assertIn("is not available for this account", fallback["message"])
            self.assertTrue(any("공급자 연결·인증 문제" in note for note in result["warnings"]))

    def test_unavailable_model_without_fallback_keeps_the_aside_message(self):
        scene = scenes.default_scene()
        scene["unavailable_models"] = ["fake-provider/fake-model"]
        with scenes.fake_aside(scene) as (settings, root, _):
            result = run(settings, root)
            self.assertEqual(result["reason"], "primary:research_unavailable")
            errors = result["run"]["sessions"]["primary"]["errors"]
            self.assertTrue(errors and "is not available for this account" in errors[0], errors)

    def test_other_errors_do_not_switch_models(self):
        scene = scenes.default_scene()
        scene["exec"]["primary"] = {"errors": ["404 model not found: fake-model"], "work": False,
                                    "final_text": ""}
        with scenes.fake_aside(scene) as (settings, root, calls):
            result = run(replace(settings, fallback_model="backup-provider/backup-model"), root)
            self.assertTrue(result["reason"].startswith("primary:"), result["reason"])
            models = {call[call.index("--model") + 1] for call in exec_calls(calls)}
            self.assertEqual(models, {"fake-provider/fake-model"})
            self.assertIsNone(result["run"]["model_fallback"])

    def test_review_result_for_other_input_is_rejected(self):
        scene = scenes.default_scene()
        bad = copy.deepcopy(scene["exec"]["review"]["result"])
        bad["review_run_id"] = "00000000-0000-0000-0000-000000000000"
        scene["exec"]["review"]["result"] = bad
        with scenes.fake_aside(scene) as (settings, root, _):
            result = run(settings, root)
            self.assertEqual(result["status"], "failed")
            self.assertIn("result_review_run_id_mismatch", result["reason"])
            self.assertEqual(result["partial"]["primary"]["outcome"], "found")


class CommandLineTests(unittest.TestCase):
    def test_search_and_verify_commands(self):
        with scenes.fake_aside(scenes.default_scene()) as (settings, root, _):
            config = root / "cpf.toml"
            config.write_text(
                "[aside]\n"
                f"model = {json.dumps(settings.model)}\neffort = \"medium\"\n"
                f"executable = {json.dumps(settings.executable)}\nhome = {json.dumps(settings.home)}\n"
                "launch_interval = 0\n", encoding="utf-8")
            # 가짜 CLI는 파이썬 스크립트라 명령줄에서는 launcher를 줄 수 없다. 설정 읽기와 입력 오류만 본다.
            self.assertEqual(main(["search", "--title", h.TITLE, "--venue", h.VENUE, "--config", str(config)]), 2)
            self.assertEqual(main(["search", "--title", h.TITLE, "--venue", h.VENUE, "--author", "A",
                                   "--year", "26", "--config", str(config)]), 2)
            result = run(settings, root)
            path = Path(result["run"]["out_dir"]) / "result.json"
            self.assertEqual(main(["verify", str(path)]), 0)
            saved = json.loads(path.read_text(encoding="utf-8"))
            saved["proof"]["verdict"]["accepted"]["venue_name"] = "Changed"
            path.write_text(json.dumps(saved, ensure_ascii=False), encoding="utf-8")
            self.assertEqual(main(["verify", str(path)]), 1)

    def test_model_is_required(self):
        self.assertEqual(main(["search", "--title", h.TITLE, "--venue", h.VENUE, "--author", "A",
                               "--effort", "medium"]), 2)
        self.assertEqual(main(["search", "--title", h.TITLE, "--venue", h.VENUE, "--author", "A",
                               "--model", "p/m"]), 2)


if __name__ == "__main__":
    unittest.main()
