import copy
import unittest

from pubfinder.output import build_result
from pubfinder.verdict import RECONCILE_POLICY, STRING_POLICY, judge, verify_result
from tests import helpers as h


def adopted(verdict: dict) -> set[str]:
    return set(verdict["accepted"])


def rules(verdict: dict) -> dict[str, str]:
    return {name: entry["rule"] for name, entry in verdict["fields"].items() if not entry["adopted"]}


class ReconcilePolicyTests(unittest.TestCase):
    def test_all_agree_with_year_adopts_everything_found(self):
        verdict = judge(h.bundle(h.make_test_query(), h.observations(), h.observations(), reconcile=h.agree_all()))
        self.assertEqual(verdict["policy"], RECONCILE_POLICY)
        self.assertTrue(verdict["core_verified"])
        self.assertEqual(len(verdict["accepted"]), 12)
        self.assertEqual(rules(verdict), {"title_ko": "unconfirmed"})
        self.assertEqual(verdict["fields"]["host_country"]["country_code"], "KR")

    def test_year_is_read_from_venue_name_when_not_given(self):
        query = h.make_test_query(year="")
        self.assertEqual((query.year, query.year_source), ("2026", "venue_name"))
        verdict = judge(h.bundle(query, h.observations(), h.observations(), reconcile=h.agree_all()))
        self.assertEqual(len(verdict["accepted"]), 12)

    def test_without_any_year_country_and_scope_are_not_adopted(self):
        query = h.make_test_query(year="", venue="IMAGCONF")
        self.assertEqual(query.year, "")
        verdict = judge(h.bundle(query, h.observations(), h.observations(), reconcile=h.agree_all()))
        self.assertEqual(len(verdict["accepted"]), 10)
        self.assertEqual(rules(verdict)["host_country"], "event_year_unconfirmed")
        self.assertEqual(rules(verdict)["conference_scope"], "event_year_unconfirmed")

    def test_different_input_year_blocks_only_country_and_scope(self):
        verdict = judge(h.bundle(h.make_test_query(year="2025"), h.observations(), h.observations(),
                                 reconcile=h.agree_all()))
        self.assertIn("presentation_date", adopted(verdict))
        self.assertEqual(rules(verdict)["host_country"], "event_year_unconfirmed")
        self.assertEqual(len(verdict["accepted"]), 10)

    def test_review_differs_on_session_and_misses_time(self):
        verdict = judge(h.bundle(h.make_test_query(), h.observations(),
                                 h.observations(session="C1: Other Session", with_time=False),
                                 reconcile=h.agree_all(disagree=("session",))))
        self.assertEqual(rules(verdict)["session"], "reconcile_disagree")
        self.assertEqual(rules(verdict)["presentation_time"], "one_sided")
        self.assertEqual(len(verdict["accepted"]), 10)
        self.assertEqual([item["value"] for item in verdict["conflicts"]["session"]],
                         ["B2: Probing Methods", "C1: Other Session"])

    def test_quote_missing_from_capture_is_recorded_for_organizer_but_blocks_country(self):
        def off_page() -> dict:
            obs = h.observations()
            obs["fields"]["organizer"]["evidence"] = [
                h.ev(h.EVENT_URL, "Organized by a society that the page never names", "official_event")]
            obs["fields"]["host_country"]["evidence"] = [
                h.ev(h.EVENT_URL, "will be held in an unnamed hall, Republic of Korea", "official_event")]
            return obs

        verdict = judge(h.bundle(h.make_test_query(), off_page(), off_page(), reconcile=h.agree_all()))
        self.assertIn("organizer", adopted(verdict))
        self.assertFalse(verdict["fields"]["organizer"]["quote_verified"])
        self.assertEqual(rules(verdict)["host_country"], "capture_unverified")

    def test_identity_disagreement_keeps_event_fields_only(self):
        verdict = judge(h.bundle(h.make_test_query(), h.observations(), h.observations(),
                                 reconcile=h.agree_all(identity="disagree")))
        self.assertFalse(verdict["core_verified"])
        self.assertEqual(rules(verdict)["presentation_date"], "identity_not_agreed")
        self.assertIn("venue_name", adopted(verdict))
        self.assertIn("host_country", adopted(verdict))
        self.assertNotIn("title_en", adopted(verdict))

    def test_primary_not_found_adopts_nothing(self):
        verdict = judge(h.bundle(h.make_test_query(), h.observations(), h.observations(), reconcile=h.agree_all(),
                                 outcome="not_found"))
        self.assertEqual(verdict["accepted"], {})
        self.assertEqual(set(rules(verdict).values()), {"outcome_not_found"})

    def test_online_only_country_is_not_adopted(self):
        obs = h.observations()
        obs["fields"]["host_country"] = h.unknown("online only event", country_code="")
        obs["fields"]["host_country"]["scope"] = "online_only"
        verdict = judge(h.bundle(h.make_test_query(), obs, h.observations(), reconcile=h.agree_all()))
        self.assertEqual(rules(verdict)["host_country"], "country_online_only")

    def test_korean_place_rule_follows_setting(self):
        place = "개최 장소: 부산 해운대구 가상컨벤션센터 제1전시장 3층"
        page = h.PAGES[h.EVENT_URL] + " " + place

        def korean_place() -> dict:
            obs = h.observations()
            obs["fields"]["host_country"] = h.claim("대한민국", "domestic_address",
                                                    [h.ev(h.EVENT_URL, place, "official_event")], country_code="KR")
            return obs

        captures = {h.EVENT_URL: h.page_capture(h.EVENT_URL, page), h.PROGRAM_URL: h.page_capture(h.PROGRAM_URL)}
        on = judge(h.bundle(h.make_test_query(), korean_place(), korean_place(), captures=captures,
                            reconcile=h.agree_all()))
        self.assertIn("host_country", adopted(on))
        off = judge(h.bundle(h.make_test_query(), korean_place(), korean_place(), captures=captures,
                             reconcile=h.agree_all(), korean=False))
        self.assertEqual(rules(off)["host_country"], "korean_place_rule_off")

    def test_organizer_address_is_not_country_evidence(self):
        quote = "Organized by the Imaginary Results Society, Busan, Republic of Korea"

        def organizer_address() -> dict:
            obs = h.observations()
            obs["fields"]["host_country"]["evidence"] = [h.ev(h.EVENT_URL, quote, "official_event")]
            return obs

        page = h.PAGES[h.EVENT_URL] + " " + quote
        captures = {h.EVENT_URL: h.page_capture(h.EVENT_URL, page), h.PROGRAM_URL: h.page_capture(h.PROGRAM_URL)}
        verdict = judge(h.bundle(h.make_test_query(), organizer_address(), organizer_address(), captures=captures,
                                 reconcile=h.agree_all()))
        self.assertEqual(rules(verdict)["host_country"], "country_direct_evidence_missing")


class StringPolicyTests(unittest.TestCase):
    def test_without_reconcile_decision_uses_string_policy(self):
        verdict = judge(h.bundle(h.make_test_query(), h.observations(), h.observations()))
        self.assertEqual(verdict["policy"], STRING_POLICY)
        self.assertTrue(verdict["core_verified"])
        self.assertEqual(len(verdict["accepted"]), 12)

    def test_without_year_nothing_is_adopted(self):
        verdict = judge(h.bundle(h.make_test_query(year="", venue="IMAGCONF"), h.observations(), h.observations()))
        self.assertFalse(verdict["core_verified"])
        self.assertEqual(verdict["accepted"], {})
        self.assertEqual(rules(verdict)["venue_name"], "core_not_verified")

    def test_leading_the_in_venue_name_is_same_value(self):
        review = h.observations()
        review["fields"]["venue_name"]["value"] = "9th International Conference on Imaginary Results (IMAGCONF 2026)"
        entry = judge(h.bundle(h.make_test_query(), h.observations(), review))["fields"]["venue_name"]
        self.assertEqual((entry["adopted"], entry["rule"]), (True, "venue_leading_the"))
        review["fields"]["venue_name"]["value"] = "The 8th International Conference on Imaginary Results (IMAGCONF 2026)"
        entry = judge(h.bundle(h.make_test_query(), h.observations(), review))["fields"]["venue_name"]
        self.assertEqual((entry["adopted"], entry["rule"]), (False, "values_differ"))

    def test_presentation_date_outside_event_period_blocks_everything(self):
        def shifted() -> dict:
            obs = h.observations()
            obs["fields"]["event_end"]["value"] = "2026-06-09"
            return obs

        verdict = judge(h.bundle(h.make_test_query(), shifted(), shifted()))
        self.assertFalse(verdict["core_verified"])
        self.assertEqual(verdict["accepted"], {})
        self.assertIn("presentation_date", verdict["conflicts"])

    def test_quote_not_in_capture_blocks_field(self):
        obs = h.observations()
        obs["fields"]["organizer"]["evidence"] = [h.ev(h.EVENT_URL, "Organized by a society never named here",
                                                       "official_event")]
        verdict = judge(h.bundle(h.make_test_query(), obs, copy.deepcopy(obs)))
        self.assertEqual(verdict["fields"]["organizer"]["rule"], "capture_unverified")


class BundleIntegrityTests(unittest.TestCase):
    def test_saved_result_verifies_and_tampering_fails(self):
        query = h.make_test_query()
        bundle = h.bundle(query, h.observations(), h.observations(), reconcile=h.agree_all())
        verdict = judge(bundle)
        result = build_result(query, bundle=bundle, verdict=verdict, run={}, captures=bundle["captures"], warnings=[])
        self.assertTrue(verify_result(result))
        tampered = copy.deepcopy(result)
        tampered["proof"]["bundle"]["review"]["fields"]["session"]["value"] = "Z9: Changed"
        self.assertFalse(verify_result(tampered))

    def test_same_session_for_both_reports_is_rejected(self):
        bundle = h.bundle(h.make_test_query(), h.observations(), h.observations(), reconcile=h.agree_all())
        bundle["review"]["session_id"] = bundle["primary"]["session_id"]
        verdict = judge(bundle)
        self.assertEqual(verdict["policy"], "invalid")
        self.assertEqual(verdict["accepted"], {})

    def test_changed_input_is_rejected(self):
        bundle = h.bundle(h.make_test_query(), h.observations(), h.observations(), reconcile=h.agree_all())
        bundle["input"]["title_en"] = "Another Title"
        self.assertEqual(judge(bundle)["error"], "bundle_input_changed")


class OutputTests(unittest.TestCase):
    def test_korean_author_input_is_shown_as_input_difference(self):
        query = h.make_test_query(authors=["홍길동", "김예시"])
        bundle = h.bundle(query, h.observations(), h.observations(), reconcile=h.agree_all())
        result = build_result(query, bundle=bundle, verdict=judge(bundle), run={}, captures=bundle["captures"],
                              warnings=[])
        self.assertEqual(result["status"], "verified")
        self.assertEqual(result["input_differences"], ["observed_authors"])
        self.assertFalse(result["input_comparison"]["observed_authors"]["same"])
        self.assertTrue(result["input_comparison"]["title_en"]["same"])
        self.assertTrue(any("입력과 다르게" in note for note in result["warnings"]))

    def test_venue_acronym_input_is_same_as_full_name(self):
        # 실제 조사(2026-11-15)에서 입력 약칭 ICASSP 2026과 채택된 정식 이름을 다른 이름으로 보고 경고를 띄웠다.
        from pubfinder.output import _same
        full = "2026 IEEE International Conference on Acoustics, Speech, and Signal Processing"
        self.assertTrue(_same("venue_name", "ICASSP 2026", full))
        self.assertTrue(_same("venue_name", "ICASSP", full))
        self.assertFalse(_same("venue_name", "ICASSP 2025", full))
        self.assertFalse(_same("venue_name", "ICML 2026", full))
        self.assertFalse(_same("venue_name", "IC 2026", full))
        self.assertTrue(_same("venue_name", "IMAGCONF 2026", "The 9th International Conference (IMAGCONF 2026)"))

    def test_wrong_title_input_is_visible_even_when_adopted(self):
        query = h.make_test_query(title="A Completely Different Title That Was Typed")
        bundle = h.bundle(query, h.observations(), h.observations(), reconcile=h.agree_all())
        result = build_result(query, bundle=bundle, verdict=judge(bundle), run={}, captures=bundle["captures"],
                              warnings=[])
        self.assertIn("title_en", result["input_differences"])

    def test_field_entry_carries_both_candidates_and_reason(self):
        query = h.make_test_query()
        bundle = h.bundle(query, h.observations(), h.observations(with_time=False), reconcile=h.agree_all())
        result = build_result(query, bundle=bundle, verdict=judge(bundle), run={}, captures=bundle["captures"],
                              warnings=[])
        item = result["fields"]["presentation_time"]
        self.assertEqual(item["status"], "not_adopted")
        self.assertEqual(item["reason"], "one_sided")
        self.assertEqual(item["candidates"]["primary"]["value"], "14:00-15:30")
        self.assertEqual(item["candidates"]["review"]["value"], "")
        self.assertEqual(result["fields"]["host_country"]["country_code"], "KR")


if __name__ == "__main__":
    unittest.main()
