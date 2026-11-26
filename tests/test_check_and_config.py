import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from pubfinder.aside.settings import SettingsError
from pubfinder.check import PAGE_URL, PDF_URL, check_aside
from pubfinder.config import load
from tests.fakes import scenes


def check_scene() -> dict:
    scene = scenes.default_scene()
    # 2026-11 실제 화면처럼 점검 문구가 제목에만 있다.
    scene["pages"][PAGE_URL] = {"title": "Example Domain", "text": "This domain is for use in documentation examples."}
    scene["files"][PDF_URL] = scenes.pdf_entry("Dummy PDF file")
    scene["exec"]["check"] = {"result": {"ok": True, "run_id": "{run_id}"}}
    return scene


class CheckAsideTests(unittest.TestCase):
    def test_basic_check_does_not_start_a_model_session(self):
        with scenes.fake_aside(check_scene()) as (settings, root, calls):
            report = check_aside(settings, out_dir=root / "check", resolve=scenes.fake_resolve)
            self.assertTrue(report["ok"], report)
            self.assertEqual(report["aside_version"], "9.9.9-fake")
            self.assertEqual([call for call in calls() if call[:1] == ["exec"]], [])

    def test_check_with_exec_reads_result_file_and_record(self):
        with scenes.fake_aside(check_scene()) as (settings, root, calls):
            report = check_aside(settings, with_exec=True, out_dir=root / "check", resolve=scenes.fake_resolve)
            self.assertTrue(report["ok"], report)
            names = {item["name"] for item in report["checks"]}
            self.assertTrue({"exec_session_id", "session_folder", "result_file", "transcript"} <= names)

    def test_check_out_of_credit_shows_reason(self):
        scene = check_scene()
        scene["out_of_credit_models"] = ["fake-provider/fake-model"]
        with scenes.fake_aside(scene) as (settings, root, calls):
            report = check_aside(settings, with_exec=True, out_dir=root / "check", resolve=scenes.fake_resolve)
            self.assertFalse(report["ok"])
            rows = {item["name"]: item for item in report["checks"]}
            self.assertEqual([name for name, item in rows.items() if not item["ok"]], ["result_file", "transcript"])
            self.assertEqual(rows["transcript"]["detail"]["kind"], "model_out_of_credit")
            self.assertIn("Your credit balance is too low", rows["transcript"]["detail"]["reason"])
            self.assertNotIn("{", rows["transcript"]["detail"]["reason"])
            self.assertEqual(rows["result_file"]["detail"]["kind"], "model_out_of_credit")
            self.assertNotIn("model_fallback", rows)
            self.assertEqual(len([call for call in calls() if call[:1] == ["exec"]]), 1)

    def test_check_unavailable_model_shows_aside_message(self):
        scene = check_scene()
        scene["unavailable_models"] = ["fake-provider/fake-model"]
        with scenes.fake_aside(scene) as (settings, root, _):
            report = check_aside(settings, with_exec=True, out_dir=root / "check", resolve=scenes.fake_resolve)
            rows = {item["name"]: item for item in report["checks"]}
            self.assertEqual(rows["transcript"]["detail"]["kind"], "model_auth")
            self.assertIn("is not available for this account", rows["transcript"]["detail"]["reason"])

    def test_check_out_of_credit_switches_to_fallback_model(self):
        scene = check_scene()
        scene["out_of_credit_models"] = ["fake-provider/fake-model"]
        with scenes.fake_aside(scene) as (settings, root, calls):
            settings = replace(settings, fallback_model="backup-provider/backup-model")
            report = check_aside(settings, with_exec=True, out_dir=root / "check", resolve=scenes.fake_resolve)
            self.assertTrue(report["ok"], report)
            rows = {item["name"]: item for item in report["checks"]}
            self.assertIn("backup-provider/backup-model", rows["model_fallback"]["detail"]["reason"])
            self.assertEqual(rows["exec_session_id"]["detail"]["model"], "backup-provider/backup-model")
            models = [call[call.index("--model") + 1] for call in calls() if call[:1] == ["exec"]]
            self.assertEqual(models, ["fake-provider/fake-model", "backup-provider/backup-model"])

    def test_check_reports_broken_capture(self):
        scene = check_scene()
        scene["pages"][PAGE_URL] = {"title": "Other", "text": "Something else entirely"}
        with scenes.fake_aside(scene) as (settings, root, _):
            report = check_aside(settings, out_dir=root / "check", resolve=scenes.fake_resolve)
            self.assertFalse(report["ok"])
            failed = [item["name"] for item in report["checks"] if not item["ok"]]
            self.assertEqual(failed, ["repl_page_capture"])


class ConfigTests(unittest.TestCase):
    def write(self, text: str) -> Path:
        folder = Path(tempfile.mkdtemp())
        path = folder / "cpf.toml"
        path.write_text(text, encoding="utf-8")
        return path

    def test_values_and_types(self):
        aside, research = load(self.write(
            '[aside]\nmodel = "p/m"\neffort = "low"\nfallback_model = "q/n"\nresearch_timeout = 900\nexecutable = ""\n'
            '[research]\nexcluded_domains = ["arxiv.org", "example.net"]\nkorean_place_as_korea = false\n'))
        self.assertEqual(aside, {"model": "p/m", "effort": "low", "fallback_model": "q/n", "research_timeout": 900.0})
        self.assertEqual(research["excluded_domains"], ("arxiv.org", "example.net"))
        self.assertFalse(research["korean_place_as_korea"])

    def test_unknown_or_wrong_values_are_rejected(self):
        with self.assertRaises(SettingsError):
            load(self.write('[aside]\nmodle = "p/m"\n'))
        with self.assertRaises(SettingsError):
            load(self.write('[aside]\nresearch_timeout = "long"\n'))
        with self.assertRaises(SettingsError):
            load(self.write('[other]\nx = 1\n'))
        with self.assertRaises(SettingsError):
            load(Path(tempfile.mkdtemp()) / "missing.toml")


if __name__ == "__main__":
    unittest.main()
