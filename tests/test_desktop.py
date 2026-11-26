"""Windows 창 시험. 동작은 `Controller`로 보고(가짜 Aside CLI를 실제 하위 프로세스로 부른다), 창은 띄워서 그려지는지만 본다."""

import json
import os
import tempfile
import time
import unittest
import uuid
from contextlib import contextmanager
from pathlib import Path

from pubfinder.app import single_instance
from pubfinder.aside.settings import SettingsError
from pubfinder.desktop.controller import BatchError, Controller, split_authors
from pubfinder.desktop.jobs import JobManager
from pubfinder.desktop.labels import hint_for, run_time
from pubfinder.desktop.storage import Store, export_csv, parse_batch_csv
from pubfinder.query import QueryError
from tests import helpers as h
from tests.fakes import scenes
from tests.test_check_and_config import check_scene

FAKE_KEYS = ("launcher", "executable", "home", "launch_interval", "research_timeout", "reconcile_timeout",
             "capture_timeout", "stop_timeout")


@contextmanager
def running(scene: dict):
    with scenes.fake_aside(scene) as (settings, root, calls):
        store = Store(root / "data", settings_overrides={key: getattr(settings, key) for key in FAKE_KEYS})
        jobs = JobManager(store, resolve=scenes.fake_resolve)
        opened: list[Path] = []
        controller = Controller(store, jobs, opener=opened.append)
        try:
            yield controller, store, opened, calls
        finally:
            controller.shutdown(30)


def wait_job(controller: Controller, job_id: str, timeout: float = 90) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = controller.job(job_id)
        if job and job["state"] in {"done", "failed", "cancelled"}:
            return job
        time.sleep(0.3)
    raise AssertionError("작업이 끝나지 않았습니다")


def configure(controller: Controller) -> None:
    controller.save_settings({"model": "fake-provider/fake-model", "effort": "medium"})


class FlowTests(unittest.TestCase):
    def test_settings_roundtrip(self):
        with running(scenes.default_scene()) as (controller, store, _, __):
            self.assertFalse(controller.state()["settings"]["configured"])
            saved = controller.save_settings({"model": "p/m", "effort": "low", "executable": "C:\\Tools\\Aside\\aside.exe",
                                              "research_timeout": 900, "korean_place_as_korea": False})
            self.assertEqual(saved["executable"], "C:\\Tools\\Aside\\aside.exe")
            self.assertTrue(saved["configured"])
            self.assertFalse(store.options().korean_place_as_korea)
            # 시험은 가짜 Aside용 제한 시간을 덮어쓰므로 저장된 파일 값을 본다.
            self.assertEqual(store.load_config()[0]["research_timeout"], 900.0)
            with self.assertRaises(ValueError):
                controller.save_settings({"research_timeout": -5})

    def test_search_job_end_to_end(self):
        with running(scenes.default_scene()) as (controller, store, opened, __):
            configure(controller)
            job = controller.submit_search(h.TITLE, h.VENUE, "Alex Sample\nRiley Fixture", "2026")
            done = wait_job(controller, job.id)
            item = done["items"][0]
            self.assertEqual((done["state"], item["state"]), ("done", "done"))
            self.assertEqual(item["authors"], h.AUTHORS)
            self.assertEqual(item["summary"]["status"], "verified")
            self.assertEqual(item["summary"]["presentation_date"], "2026-06-10")
            self.assertEqual([row["run_id"] for row in controller.results()], [item["run_id"]])
            self.assertEqual(controller.result(item["run_id"])["fields"]["host_country"]["country_code"], "KR")
            target = store.data_dir / "out.csv"
            self.assertEqual(controller.export([item["run_id"]], target), 1)
            raw = target.read_bytes()
            self.assertTrue(raw.startswith("\ufeff실행 ID,상태,입력 논문명".encode("utf-8")))
            self.assertIn("2026-06-10", raw.decode("utf-8"))
            self.assertNotIn(b"\r\r\n", raw)
            controller.open_folder(item["run_id"])
            self.assertEqual(opened[-1].name, item["run_id"])

    def test_search_input_error(self):
        with running(scenes.default_scene()) as (controller, _, __, ___):
            with self.assertRaises(QueryError) as caught:
                controller.submit_search(h.TITLE, h.VENUE, "")
            self.assertIn("저자명", str(caught.exception))

    def test_missing_model_is_refused_before_queueing(self):
        csv_text = f"title,venue,authors\n{h.TITLE},{h.VENUE},Alex Sample\n"
        with running(scenes.default_scene()) as (controller, _, __, calls):
            # 모델은 화면 기본값이 있고 추론 강도는 없다.
            with self.assertRaises(SettingsError) as caught:
                controller.submit_search(h.TITLE, h.VENUE, h.AUTHORS)
            self.assertIn("추론 강도를 정하세요", str(caught.exception))
            with self.assertRaises(SettingsError):
                controller.submit_batch(csv_text)
            self.assertEqual(controller.jobs.recent(), [])
            self.assertEqual(calls(), [])
            # 모델 칸을 비워 저장하면 기본값으로 되돌리지 않는다.
            controller.save_settings({"model": "", "effort": "medium"})
            with self.assertRaises(SettingsError) as caught:
                controller.submit_search(h.TITLE, h.VENUE, h.AUTHORS)
            self.assertIn("모델을 정하세요", str(caught.exception))

    def test_settings_cleared_after_queueing_fails_the_item_with_guidance(self):
        with running(scenes.default_scene()) as (controller, _, __, ___):
            job = controller.jobs.submit_queries([{"title": h.TITLE, "venue": h.VENUE, "authors": h.AUTHORS}], "search")
            item = wait_job(controller, job.id)["items"][0]
            self.assertEqual(item["state"], "failed")
            self.assertIn("추론 강도를 정하세요", item["error"])

    def test_batch_job_skips_bad_rows(self):
        csv_text = ("\ufeff논문명,학술대회명,저자,연도\n"
                    f"{h.TITLE},{h.VENUE},Alex Sample; Riley Fixture,2026\n"
                    f"{h.TITLE},,Alex Sample,2026\n")
        with running(scenes.default_scene()) as (controller, _, __, ___):
            configure(controller)
            job, errors = controller.submit_batch(csv_text)
            self.assertEqual(len(errors), 1)
            self.assertIn("3번째 줄", errors[0])
            done = wait_job(controller, job.id)
            self.assertEqual([item["state"] for item in done["items"]], ["done"])
            self.assertEqual(done["items"][0]["authors"], ["Alex Sample", "Riley Fixture"])
            with self.assertRaises(BatchError) as caught:
                controller.submit_batch("title,venue,authors\n")
            self.assertEqual(str(caught.exception), "조사할 줄이 없습니다")

    def test_check_job(self):
        with running(check_scene()) as (controller, _, __, ___):
            configure(controller)
            done = wait_job(controller, controller.submit_check(with_exec=False).id)
            self.assertTrue(done["report"]["ok"], done)
            # 점검 결과에 실제로 쓸 모델을 보여 준다.
            self.assertEqual(done["report"]["checks"][0],
                             {"name": "settings", "ok": True,
                              "detail": "fake-provider/fake-model · medium · 못 쓸 때 anthropic/claude-sonnet-5-5"})

    def test_check_without_saved_model_does_not_pass(self):
        # Aside가 준비돼 있어도 추론 강도가 없으면 조사를 못 하니 점검을 통과로 보이면 안 된다.
        with running(check_scene()) as (controller, _, __, calls):
            for with_exec in (False, True):
                done = wait_job(controller, controller.submit_check(with_exec=with_exec).id)
                self.assertEqual(done["error"], "")
                report = done["report"]
                self.assertFalse(report["ok"], report)
                first, rest = report["checks"][0], report["checks"][1:]
                self.assertEqual((first["name"], first["ok"]), ("settings", False))
                self.assertIn("추론 강도를 정하세요", first["detail"])
                self.assertTrue(rest and all(item["ok"] for item in rest), rest)
                self.assertFalse(report["with_exec"])
            # 모델을 부르는 점검은 설정이 갖춰질 때까지 세션을 띄우지 않는다.
            self.assertEqual([c for c in calls() if c[:1] == ["exec"]], [])

    def test_cancel_stops_the_running_search(self):
        scene = scenes.default_scene()
        scene["exec"]["primary"]["sleep"] = 30
        scene["exec"]["review"]["sleep"] = 30
        with running(scene) as (controller, _, __, calls):
            configure(controller)
            job = controller.submit_search(h.TITLE, h.VENUE, h.AUTHORS)
            for _ in range(60):
                if "조사 중" in (controller.job(job.id)["items"][0]["stage"] or ""):
                    break
                time.sleep(0.2)
            time.sleep(1.5)
            started = time.monotonic()
            self.assertTrue(controller.cancel(job.id))
            done = wait_job(controller, job.id, timeout=40)
            self.assertLess(time.monotonic() - started, 25)
            self.assertEqual((done["state"], done["items"][0]["state"]), ("cancelled", "cancelled"))
            self.assertTrue([c for c in calls() if c[:2] == ["session", "stop"]])

    def test_open_only_inside_run_folders(self):
        with running(scenes.default_scene()) as (controller, store, opened, _):
            with self.assertRaises(ValueError):
                controller.open_folder("..\\..\\Windows")
            controller.open_folder()
            self.assertEqual(opened, [store.runs_dir])

    def test_sample_csv(self):
        with running(scenes.default_scene()) as (controller, store, _, __):
            target = store.data_dir / "sample.csv"
            controller.save_sample_csv(target)
            rows, errors = parse_batch_csv(target.read_text(encoding="utf-8"))
            self.assertEqual((len(rows), errors), (2, []))


class StorageTests(unittest.TestCase):
    def test_batch_csv_headers_and_errors(self):
        rows, errors = parse_batch_csv("title,venue,authors\nT1,V 2026,A; B\n")
        self.assertEqual(rows, [{"title": "T1", "venue": "V 2026", "authors": ["A", "B"], "year": ""}])
        self.assertEqual(errors, [])
        rows, errors = parse_batch_csv("제목만\nx\n")
        self.assertEqual(rows, [])
        self.assertIn("칸 이름", errors[0])
        rows, errors = parse_batch_csv("title,venue,authors,year\nT,V,A,26\n")
        self.assertEqual(rows, [])
        self.assertIn("2번째 줄", errors[0])

    def test_export_leaves_unadopted_blank(self):
        result = {"status": "partial", "status_text": "일부", "query": {"title": "T", "venue": "V", "authors": ["A"]},
                  "fields": {"presentation_date": {"status": "not_adopted", "value": ""},
                             "venue_name": {"status": "adopted", "value": "Venue 2026"}},
                  "summary": {"adopted_count": 1}, "warnings": ["주의 하나"]}
        lines = export_csv([("20261115T101500-1a2b3c4d", result)]).splitlines()
        self.assertIn("Venue 2026", lines[1])
        self.assertIn("주의 하나", lines[1])

    def test_save_keeps_other_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp))
            store.config_path.write_text('[aside]\nmodel = "a/b"\naccount = "u1"\n[research]\nrun_deadline = 600\n',
                                         encoding="utf-8")
            store.save_settings({"effort": "high"})
            aside, research = store.load_config()
            self.assertEqual((aside["model"], aside["account"], aside["effort"]), ("a/b", "u1", "high"))
            self.assertEqual(research["run_deadline"], 600.0)

    def test_model_defaults_and_fallback_can_be_cleared(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp))
            # 아무것도 저장하지 않으면 Claude 계정 모델을 쓰고, 못 쓰면 Anthropic API 모델로 넘어간다.
            self.assertEqual((store.settings().model, store.settings().fallback_model),
                             ("claude-code/claude-sonnet-5-5", "anthropic/claude-sonnet-5-5"))
            self.assertEqual(store.settings_view()["model"], "claude-code/claude-sonnet-5-5")
            self.assertFalse(store.settings_view()["configured"])
            store.save_settings({"model": "a/b", "effort": "low", "fallback_model": " c/d "})
            self.assertEqual(store.settings().fallback_model, "c/d")
            self.assertEqual(store.settings_view()["fallback_model"], "c/d")
            self.assertTrue(store.settings_view()["configured"])
            # 비워 저장하면 "바꾸지 않음"이다. 기본값으로 되돌아가지 않는다.
            store.save_settings({"fallback_model": ""})
            self.assertEqual(store.load_config()[0]["fallback_model"], "")
            self.assertEqual(store.settings().fallback_model, "")


class HelperTests(unittest.TestCase):
    def test_split_authors_and_labels(self):
        self.assertEqual(split_authors("Alex Sample; Riley Fixture\n\n 홍길동 "), ["Alex Sample", "Riley Fixture", "홍길동"])
        self.assertEqual(split_authors([" A ", ""]), ["A"])
        self.assertEqual(run_time("20261125T170512-adc343dc"), "2026-11-25 17:05")
        self.assertEqual(run_time("x"), "")
        self.assertIn("잔액", hint_for("primary:research_unavailable:out_of_credit"))
        self.assertEqual(hint_for("뭔지 모를 오류"), "")

    def test_cp949_csv_is_read(self):
        from pubfinder.desktop.window import read_csv_file

        with tempfile.TemporaryDirectory() as tmp:
            utf8, legacy = Path(tmp) / "a.csv", Path(tmp) / "b.csv"
            utf8.write_bytes("\ufeff논문명,학술대회명,저자\n".encode("utf-8"))
            legacy.write_bytes("논문명,학술대회명,저자\n".encode("cp949"))
            self.assertEqual(read_csv_file(utf8), ("논문명,학술대회명,저자\n", ""))
            text, note = read_csv_file(legacy)
            self.assertEqual(text, "논문명,학술대회명,저자\n")
            self.assertIn("CP949", note)


@unittest.skipUnless(os.name == "nt", "Windows 뮤텍스 시험")
class SingleInstanceTests(unittest.TestCase):
    def test_second_launch_is_refused(self):
        name = f"Local\\pubfinder-test-{uuid.uuid4().hex}"
        first = single_instance(name)
        self.assertIsNotNone(first)
        self.assertIsNone(single_instance(name))
        import ctypes

        ctypes.WinDLL("kernel32").CloseHandle(first)
        again = single_instance(name)
        self.assertIsNotNone(again)
        ctypes.WinDLL("kernel32").CloseHandle(again)


class WindowTests(unittest.TestCase):
    """창을 실제로 띄워 탭과 결과 표가 그려지는지 본다. 화면이 없는 환경이면 건너뛴다."""

    def setUp(self):
        import tkinter as tk

        try:
            self.root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(f"창을 띄울 수 없습니다: {exc}")
        self.root.withdraw()

    def tearDown(self):
        try:
            self.root.destroy()
        except Exception:  # noqa: BLE001 - 이미 닫혔으면 그대로 둔다.
            pass

    def test_search_through_window_with_run_loop(self):
        """창의 조사 시작 버튼부터 결과 표까지. 이벤트 고리는 실제 창과 같은 run_loop다."""
        from pubfinder.desktop.window import App, run_loop

        with running(scenes.default_scene()) as (controller, _, __, ___):
            app = App(self.root, controller)
            app.settings_tab.effort.set("medium")
            app.settings_tab.model.set("fake-provider/fake-model")
            tab = app.search_tab
            tab.title.insert("1.0", h.TITLE)
            tab.venue.insert(0, h.VENUE)
            tab.year.insert(0, "2026")
            tab.authors.insert("1.0", "Alex Sample; Riley Fixture")
            seen: dict = {"stages": set()}

            def watch():
                if tab.job_id:
                    seen["stages"].add(tab.stage.cget("text"))
                    self.root.after(100, watch)
                    return
                view = tab.result
                seen.update(run_id=view.run_id, status=view.status.cget("text"), rows=len(view.tree.get_children()),
                            date=view.tree.item("presentation_date", "values")[1] if view.run_id else "",
                            progress=tab.progress.winfo_manager(), start=str(tab.start_button.cget("state")))
                app.close(ask=False)

            def start():
                tab.start_button.invoke()
                seen["queued"] = bool(tab.job_id)
                self.root.after(100, watch)

            self.root.after(100, start)
            self.root.after(120000, self.root.destroy)  # 끝나지 않으면 시험이 멈추지 않게 한다.
            run_loop(self.root)
            self.assertTrue(seen.get("queued"), tab.message.cget("text") if seen.get("queued") is False else seen)
            self.assertTrue(seen.get("run_id"), seen)
            self.assertIn("확인됨", seen["status"])
            self.assertEqual((seen["rows"], seen["date"], seen["progress"], seen["start"]), (13, "2026-06-10", "", "normal"))
            self.assertTrue(any("조사 중" in stage for stage in seen["stages"]), seen["stages"])

    def test_run_loop_runs_timers_and_returns_on_destroy(self):
        from pubfinder.desktop.window import run_loop

        fired = []
        self.root.after(50, lambda: fired.append("a"))
        self.root.after(150, lambda: (fired.append("b"), self.root.destroy()))
        started = time.monotonic()
        run_loop(self.root)
        self.assertEqual(fired, ["a", "b"])
        self.assertLess(time.monotonic() - started, 5)

    def test_window_draws_tabs_results_and_closes(self):
        from pubfinder.desktop.labels import FIELD_LABELS
        from pubfinder.desktop.window import App

        with running(scenes.default_scene()) as (controller, store, _, __):
            run_id = "20261125T170512-adc343dc"
            folder = store.runs_dir / run_id
            folder.mkdir(parents=True)
            fields = {name: {"status": "not_adopted", "value": "", "reason_text": "두 조사 모두 확인하지 못함"}
                      for name in FIELD_LABELS}
            fields["presentation_date"] = {"status": "adopted", "value": "2026-06-10",
                                           "evidence": {"url": h.PROGRAM_URL, "quote": "Wednesday 10 June 2026",
                                                        "quote_verified": True},
                                           "candidates": {"primary": {"value": "2026-06-10"},
                                                          "review": {"value": "2026-06-10"}}}
            result = {"status": "verified", "status_text": "같은 논문의 발표일·제목까지 확인됨",
                      "query": {"title": h.TITLE, "venue": h.VENUE, "authors": h.AUTHORS, "year": "2026"},
                      "fields": fields, "summary": {"adopted_count": 1}, "warnings": ["주의 하나"]}
            (folder / "result.json").write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
            app = App(self.root, controller)
            self.root.update()
            self.assertEqual(self.root.title(), "pubfinder")
            self.assertEqual([app.notebook.tab(tab, "text").strip() for tab in app.notebook.tabs()],
                             ["한 건 조사", "여러 건 조사", "지난 결과", "설정"])
            # 추론 강도가 비어 있으면 위쪽 안내가 보인다.
            self.assertEqual(app.banner.winfo_manager(), "pack")
            app.notebook.select(app.results_tab)
            self.root.update()
            self.assertEqual(app.results_tab.tree.get_children(), (run_id,))
            app.results_tab.tree.selection_set(run_id)
            self.root.update()
            view = app.results_tab.result
            self.assertEqual(len(view.tree.get_children()), 13)
            self.assertEqual(view.tree.item("presentation_date", "values")[0], "✓ 채택")
            view.tree.selection_set("presentation_date")
            self.root.update()
            detail = view.detail.get("1.0", "end")
            self.assertIn(h.PROGRAM_URL, detail)
            self.assertIn("1차 조사: 2026-06-10", detail)
            self.assertEqual(str(view.link_button.cget("state")), "normal")
            # 설정 탭에서 저장하면 안내가 사라진다.
            app.settings_tab.effort.set("medium")
            self.assertTrue(app.settings_tab.dirty)
            self.assertTrue(app.settings_tab.save())
            self.root.update()
            self.assertEqual(app.banner.winfo_manager(), "")
            self.assertEqual(store.load_config()[0]["effort"], "medium")
            # 한 건 조사 탭의 입력 오류는 대기열에 넣지 않고 창에 보인다.
            app.search_tab.title.insert("1.0", h.TITLE)
            app.search_tab.venue.insert(0, h.VENUE)
            app.search_tab.start()
            self.assertIn("저자명", app.search_tab.message.cget("text"))
            self.assertEqual(controller.jobs.recent(), [])
            app.close(ask=False)
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                try:
                    self.root.update()
                except Exception:  # noqa: BLE001 - destroy 뒤에는 update가 실패한다.
                    break
                time.sleep(0.05)
            import tkinter as tk

            with self.assertRaises(tk.TclError):
                self.root.winfo_exists()


if __name__ == "__main__":
    unittest.main()
