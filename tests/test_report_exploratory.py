from unittest import TestCase

from scripts.report_exploratory import audited_rates, paired_rates, render_scorecard


class ReportingTests(TestCase):
    def setUp(self):
        self.rows = [
            {"group_id": group, "seed": seed, "method": method, "official": {
                "valid_rate": .5,
                "novelty_rate": .5,
                "recovery_rate": .25 if method == "ACB" else .5,
                "all_hypotheses": ["private candidate"],
            }}
            for group in ("private-id-a", "private-id-b")
            for seed in (42, 314159)
            for method in ("ACB", "FSCB")
        ]
        self.summary = {
            method: {"valid_rate": .5, "novelty_rate": .5, "recovery_rate": .25 if method == "ACB" else .5}
            for method in ("ACB", "FSCB")
        }

    def test_audit_rejects_summary_not_matching_the_official_trajectories(self):
        self.summary["FSCB"]["recovery_rate"] = .99
        with self.assertRaisesRegex(ValueError, "macro"):
            audited_rates(self.rows, self.summary, ("ACB", "FSCB"), "method")

    def test_pairing_rejects_missing_or_duplicate_trajectory(self):
        for rows in (self.rows[:-1], self.rows + self.rows[-1:]):
            with self.subTest(rows=len(rows)), self.assertRaises(ValueError):
                paired_rates(rows)

    def test_scorecard_exports_only_public_aliases_and_measured_metrics(self):
        summary = audited_rates(self.rows, self.summary, ("ACB", "FSCB"), "method")
        report = render_scorecard(summary, paired_rates(self.rows), requests=32, failures=0)
        self.assertNotIn("private-id", report)
        self.assertNotIn("private candidate", report)
        self.assertIn("G1", report)
        self.assertIn("+25.00", report)
        self.assertIn("descriptive", report)
