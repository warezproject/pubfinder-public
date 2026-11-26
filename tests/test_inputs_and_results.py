import json
import unittest

from pubfinder.prompts import (
    DONE_MARKER,
    HUMAN_VERIFICATION_RULES,
    build_primary_prompt,
    build_review_prompt,
    with_common_rules,
)
from pubfinder.query import QueryError, make_query, resolve_year
from pubfinder.reconcile import (
    MAX_PROMPT_CHARS,
    build_reconcile_prompt,
    first_json_object,
    parse_reconcile,
)
from pubfinder.results import (
    ResultError,
    evidence_urls,
    has_evidence,
    parse_primary_result,
    parse_review_result,
)
from tests import helpers as h


class QueryTests(unittest.TestCase):
    def test_year_formats(self):
        for raw in ("2026", "2026-06-10", "06/10/2026", "June 2026", "10 June 2026", "2026년 6월"):
            self.assertEqual(resolve_year(raw, "X"), ("2026", "input"), raw)

    def test_year_from_venue_only_when_single(self):
        self.assertEqual(resolve_year("", "Workshop 2025 at IMAGCONF 2026"), ("", ""))
        self.assertEqual(resolve_year("", "IMAGCONF2026"), ("2026", "venue_name"))
        self.assertEqual(resolve_year("", "IMAGCONF"), ("", ""))
        self.assertEqual(resolve_year("", "IMAGCONF '26 (2026)"), ("2026", "venue_name"))

    def test_bad_year_input_is_rejected(self):
        with self.assertRaises(QueryError):
            resolve_year("26", "X")
        with self.assertRaises(QueryError):
            resolve_year("2025-2026", "X")

    def test_required_inputs(self):
        with self.assertRaises(QueryError):
            make_query("", h.VENUE, h.AUTHORS)
        with self.assertRaises(QueryError):
            make_query(h.TITLE, "", h.AUTHORS)
        with self.assertRaises(QueryError):
            make_query(h.TITLE, h.VENUE, ["  "])

    def test_korean_title_goes_to_korean_slot(self):
        query = make_query("가상 학술대회 발표를 위한 시험 논문", "가상학술대회 2026", ["홍길동"])
        source = query.source_input()
        self.assertEqual(source["title_en"], "")
        self.assertTrue(source["title_ko"])
        self.assertEqual(source["year"], "2026")

    def test_fingerprint_changes_with_input(self):
        self.assertNotEqual(h.make_test_query().fingerprint(), h.make_test_query(year="2025").fingerprint())


class PromptTests(unittest.TestCase):
    def setUp(self):
        self.query = h.make_test_query()

    def test_primary_prompt_binds_run_and_input(self):
        prompt = build_primary_prompt(self.query.source_input(), self.query.fingerprint(), run_id="run-x",
                                      result_path="./artifacts/cpf/run-x/primary/result.json",
                                      excluded_domains=("arxiv.org",))
        self.assertIn("run_id=run-x", prompt)
        self.assertIn(self.query.fingerprint(), prompt)
        self.assertIn("./artifacts/cpf/run-x/primary/result.json", prompt)
        self.assertIn(f"{DONE_MARKER} run-x", prompt)
        self.assertIn("arxiv.org", prompt)
        self.assertIn('"year": "2026"', prompt)
        self.assertNotIn("abstracts", prompt)
        self.assertLess(len(with_common_rules(prompt)), 20000)

    def test_review_prompt_does_not_carry_primary_answers(self):
        prompt = build_review_prompt(self.query.source_input(), "review-1", run_id="run-x",
                                     result_path="./artifacts/cpf/run-x/review/result.json")
        self.assertIn("review-1", prompt)
        self.assertNotIn(self.query.fingerprint(), prompt)
        self.assertNotIn("imagconf.example.org", prompt)

    def test_korean_place_rule_can_be_turned_off(self):
        on = build_primary_prompt(self.query.source_input(), "f", run_id="r", result_path="p")
        off = build_primary_prompt(self.query.source_input(), "f", run_id="r", result_path="p",
                                   korean_place_as_korea=False)
        self.assertIn("domestic_address", on)
        self.assertNotIn("domestic_address", off)

    def test_human_verification_rules_are_prefixed(self):
        prompt = with_common_rules("본문")
        self.assertTrue(prompt.startswith(HUMAN_VERIFICATION_RULES))
        self.assertIn("Verify you are human", prompt)


class ResultParsingTests(unittest.TestCase):
    def setUp(self):
        self.query = h.make_test_query()

    def parse(self, raw: bytes, **kwargs):
        return parse_primary_result(raw, run_id="run-1", input_fingerprint=self.query.fingerprint(), **kwargs)

    def test_valid_primary_result(self):
        parsed = self.parse(h.primary_file(self.query, h.observations()))
        self.assertEqual(parsed["outcome"], "found")
        self.assertEqual(len(parsed["observations"]["fields"]), 13)
        self.assertEqual(len(parsed["trail"]), 1)
        self.assertTrue(has_evidence(parsed["observations"]))
        self.assertEqual(evidence_urls(parsed["observations"]), [h.PROGRAM_URL, h.EVENT_URL])

    def test_binding_mismatch_is_rejected(self):
        raw = h.primary_file(self.query, h.observations())
        with self.assertRaisesRegex(ResultError, "result_input_fingerprint_mismatch"):
            parse_primary_result(raw, run_id="run-1", input_fingerprint=h.make_test_query(year="2025").fingerprint())
        with self.assertRaisesRegex(ResultError, "result_run_id_mismatch"):
            parse_primary_result(raw, run_id="run-2", input_fingerprint=self.query.fingerprint())
        with self.assertRaisesRegex(ResultError, "result_review_run_id_mismatch"):
            parse_review_result(h.review_file(h.observations()), run_id="run-1", review_run_id="other")

    def test_excluded_source_is_rejected(self):
        obs = h.observations()
        obs["fields"]["organizer"]["evidence"] = [h.ev("https://arxiv.org/abs/0000.00000", "Organized by the "
                                                       "Imaginary Results Society", "official_event")]
        with self.assertRaisesRegex(ResultError, "result_evidence_url_not_allowed:organizer"):
            self.parse(h.primary_file(self.query, obs))

    def test_extra_fields_and_keys_are_dropped_with_note(self):
        obs = h.observations()
        obs["fields"]["abstracts"] = h.unknown()
        parsed = self.parse(h.primary_file(self.query, obs, program_sources=[]))
        self.assertNotIn("abstracts", parsed["observations"]["fields"])
        self.assertIn("ignored_fields:abstracts", parsed["notes"])
        self.assertIn("ignored_keys:program_sources", parsed["notes"])

    def test_invalid_date_and_claim_shape(self):
        obs = h.observations()
        obs["fields"]["event_start"]["value"] = "June 9, 2026"
        with self.assertRaisesRegex(ResultError, "result_date_invalid:event_start"):
            self.parse(h.primary_file(self.query, obs))
        obs = h.observations()
        del obs["fields"]["organizer"]["reason"]
        with self.assertRaisesRegex(ResultError, "result_claim_keys:organizer"):
            self.parse(h.primary_file(self.query, obs))

    def test_empty_observation_or_locator_does_not_reject_the_report(self):
        # 실제 측정(2026-11-15)에서 2차 조사가 근거 하나의 observation을 빈 문자열로 남겨 2차 결과 전체가 버려졌다.
        # observation·locator는 판정에 쓰지 않는 메모라 비어도 받는다. url·quote·tool·scope는 그대로 꼭 있어야 한다.
        obs = h.observations()
        obs["fields"]["venue_name"]["evidence"][0]["observation"] = ""
        obs["fields"]["organizer"]["evidence"][0]["locator"] = " "
        parsed = parse_review_result(h.review_file(obs), run_id="run-1", review_run_id="review-1")
        self.assertEqual(parsed["observations"]["fields"]["venue_name"]["evidence"][0]["observation"], "")
        for key in ("url", "quote", "tool", "scope"):
            bad = h.observations()
            bad["fields"]["venue_name"]["evidence"][0][key] = ""
            with self.assertRaisesRegex(ResultError, "result_evidence_shape:venue_name"):
                parse_review_result(h.review_file(bad), run_id="run-1", review_run_id="review-1")

    def test_country_code_is_normalized_not_rejected(self):
        obs = h.observations()
        obs["fields"]["host_country"]["country_code"] = " kr "
        parsed = self.parse(h.primary_file(self.query, obs))
        self.assertEqual(parsed["observations"]["fields"]["host_country"]["country_code"], "KR")

    def test_bad_trail_rows_are_dropped(self):
        raw = json.loads(h.primary_file(self.query, h.observations()))
        raw["trail"].append({"query": "", "url": "", "purpose": "program", "result": "confirmed"})
        raw["trail"].append({"query": "q", "url": "ftp://x", "purpose": "program", "result": "confirmed"})
        parsed = self.parse(json.dumps(raw).encode())
        self.assertEqual(len(parsed["trail"]), 1)
        self.assertIn("trail_rows_dropped:2", parsed["notes"])

    def test_not_json(self):
        with self.assertRaisesRegex(ResultError, "result_json_invalid"):
            self.parse(b"not json")

    def test_review_without_evidence(self):
        empty = {"identity": h.unknown(), "event": h.unknown(), "fields": {}, "limitations": []}
        parsed = parse_review_result(h.review_file(empty), run_id="run-1", review_run_id="review-1")
        self.assertFalse(has_evidence(parsed["observations"]))


class ReconcileTests(unittest.TestCase):
    def test_missing_fields_default_to_neither(self):
        parsed = parse_reconcile({"schema_version": "cpf-conference-reconcile-v1",
                                  "identity": {"decision": "agree", "reason": "x"}, "fields": {}})
        self.assertEqual(parsed["fields"]["venue_name"]["decision"], "neither")
        self.assertEqual(len(parsed["fields"]), 13)

    def test_bad_decision_is_rejected(self):
        with self.assertRaises(ValueError):
            parse_reconcile({"schema_version": "cpf-conference-reconcile-v1", "identity": {"decision": "maybe"},
                             "fields": {}})
        with self.assertRaises(ValueError):
            parse_reconcile({"schema_version": "other", "fields": {}})

    def test_json_inside_prose_and_fence(self):
        text = '설명입니다.\n```json\n{"schema_version": "cpf-conference-reconcile-v1", "fields": {"a": {}}}\n```'
        self.assertEqual(first_json_object(text)["schema_version"], "cpf-conference-reconcile-v1")
        with self.assertRaises(ValueError):
            first_json_object("JSON 없음 {깨짐")

    def test_prompt_shrinks_long_quotes(self):
        query = h.make_test_query()
        obs = h.observations()
        for claim in obs["fields"].values():
            if claim["evidence"]:
                claim["evidence"] = [dict(claim["evidence"][0], quote="긴 인용 " * 400) for _ in range(8)]
        prompt = build_reconcile_prompt(query.source_input(), obs, obs, primary_outcome="found")
        self.assertLessEqual(len(prompt), MAX_PROMPT_CHARS)


if __name__ == "__main__":
    unittest.main()
