import copy
import unittest

from scripts.verify_result import verify


def fixture():
    return {
        "benchmark": "LoCoMo", "overall_scope": "categories 1-4 (adversarial excluded)",
        "question_count": 3, "error_count": 0,
        "category_counts": {"single_hop": 2, "adversarial": 1},
        "results": {"single_hop": 0.5, "overall": 0.5},
        "per_question": [
            {"question_id": "a", "category": "single_hop", "category_int": 4, "score": 1, "error": None},
            {"question_id": "b", "category": "single_hop", "category_int": 4, "score": 0, "error": None},
            {"question_id": "c", "category": "adversarial", "category_int": 5, "score": None, "error": None},
        ],
    }


class ResultVerificationTests(unittest.TestCase):
    def test_recomputes_scores_and_exclusion(self):
        summary = verify(fixture(), expected_questions=3, expected_scored=2)
        self.assertEqual(summary["correct"], 1)
        self.assertEqual(summary["excluded"], 1)
        self.assertEqual(summary["results"]["overall"], 0.5)

    def test_rejects_incomplete_tampered_or_errored_artifacts(self):
        changes = [
            lambda r: r["results"].update(overall=1),
            lambda r: r.update(question_count=4),
            lambda r: r["category_counts"].update(single_hop=3),
            lambda r: r["per_question"][0].update(score=None),
            lambda r: r["per_question"][0].update(score=True),
            lambda r: r["per_question"][0].update(score=2),
            lambda r: r["per_question"][0].update(error="timeout"),
            lambda r: r["per_question"][0].update(category="temporal"),
            lambda r: r["per_question"][1].update(question_id="a"),
            lambda r: r["per_question"][2].update(score=1),
            lambda r: r.update(error_count=1),
        ]
        for index, change in enumerate(changes):
            with self.subTest(case=index):
                result = copy.deepcopy(fixture())
                change(result)
                with self.assertRaises(ValueError):
                    verify(result)

    def test_rejects_incorrect_expected_coverage(self):
        with self.assertRaisesRegex(ValueError, "question coverage"):
            verify(fixture(), expected_questions=1986)
        with self.assertRaisesRegex(ValueError, "scored coverage"):
            verify(fixture(), expected_scored=1540)

    def test_supports_explicit_adversarial_inclusion(self):
        result = fixture()
        result["overall_scope"] = "categories 1-5"
        result["per_question"][2]["score"] = 1
        result["results"].update(adversarial=1.0, overall=0.6667)
        self.assertEqual(verify(result)["scored"], 3)
