from unittest import TestCase
from unittest.mock import patch

from scripts.candidate_history import BooleanObservation, CandidateHistory, HistoryStatus
from scripts.run_d2_prompt import render as render_original_prompt


class CandidateHistoryTests(TestCase):
    def setUp(self):
        self.history = self.new_history()

    @staticmethod
    def new_history():
        return CandidateHistory([BooleanObservation({"x": 0, "y": 0}, 0)])

    def test_first_slot_uses_the_original_empty_representation(self):
        views = self.history.render(len)
        self.assertEqual((views.original.text, views.structured.text), ("None", "None"))
        self.assertEqual((views.original.token_count, views.structured.token_count), (4, 4))

    def test_original_view_matches_the_frozen_renderer(self):
        self.history.observe("Expression: x ∨ y.")
        self.history.observe("Expression: NOT x")
        self.history.observe("Expression: y OR x")
        expected = render_original_prompt(
            "{{PRIOR_EXPRESSIONS}}", "", ["x OR y", "NOT x", "y OR x"],
        )
        views = self.history.render(len)
        self.assertEqual(views.original.text, expected)
        self.assertEqual(
            views.structured.text, "Expression: x OR y\nDiagnostic: OUT_OF_SPACE",
        )

    def test_official_keys_deduplicate_commutativity_and_idempotence(self):
        for expr, expected in (
            ("x OR y", HistoryStatus.VALID_NEW),
            ("y OR x", HistoryStatus.VALID_DUPLICATE),
            ("x AND x", HistoryStatus.VALID_NEW),
            ("x", HistoryStatus.VALID_DUPLICATE),
            ("x OR (x AND y)", HistoryStatus.VALID_NEW),
        ):
            with self.subTest(expr=expr):
                self.assertIs(self.history.observe(f"Expression: {expr}"), expected)
        self.assertEqual(
            self.history.render(len).structured.text,
            "Expression: x OR y\nExpression: x AND x\nExpression: x OR (x AND y)",
        )

    def test_evicted_key_stays_seen_and_never_refreshes_window(self):
        for expr in ("x", "y", "x AND y", "x OR y", "x OR (x AND y)"):
            self.assertIs(self.history.observe(expr), HistoryStatus.VALID_NEW)
        before = self.history.render(len).structured
        self.assertEqual(
            before.text,
            "Expression: y\nExpression: x AND y\nExpression: x OR y\nExpression: x OR (x AND y)",
        )
        self.assertIs(self.history.observe("x AND x"), HistoryStatus.VALID_DUPLICATE)
        self.assertIs(self.history.observe("y"), HistoryStatus.VALID_DUPLICATE)
        self.assertEqual(self.history.render(len).structured, before)

    def test_observation_validator_uses_every_public_row(self):
        history = CandidateHistory([
            BooleanObservation({"x": 0, "y": 0}, 0),
            BooleanObservation({"x": 1, "y": 0}, 1),
        ])
        self.assertIs(history.observe("x AND y"), HistoryStatus.OBSERVATION_MISMATCH)
        self.assertIs(history.observe("x"), HistoryStatus.VALID_NEW)
        self.assertEqual(
            history.render(len).structured.text,
            "Expression: x\nDiagnostic: OBSERVATION_MISMATCH",
        )

    def test_invalid_candidates_get_only_fixed_labels_in_structured_history(self):
        for raw, expected in (
            ("# no expression", HistoryStatus.UNPARSEABLE),
            ("Expression: NOR(x,y)", HistoryStatus.OUT_OF_SPACE),
            ("Expression: z", HistoryStatus.OUT_OF_SPACE),
            ("Expression: True", HistoryStatus.OUT_OF_SPACE),
            ("Expression: x AND (y OR (x AND y))", HistoryStatus.OUT_OF_SPACE),
            ("Expression: x.__class__", HistoryStatus.OUT_OF_SPACE),
            ("", HistoryStatus.UNPARSEABLE),
        ):
            with self.subTest(raw=raw):
                self.assertIs(self.history.observe(raw), expected)
                self.assertEqual(
                    self.history.render(len).structured.text, f"None\nDiagnostic: {expected}",
                )

    def test_missing_key_does_not_enter_structured_history(self):
        with patch(
            "scripts.candidate_history.BooleanBenchmarkRefined.get_expression_mechanistic_key",
            return_value=None,
        ):
            self.assertIs(self.history.observe("x"), HistoryStatus.MISSING_MECHANISTIC_KEY)
        self.assertEqual(
            self.history.render(len).structured.text, "None\nDiagnostic: MISSING_MECHANISTIC_KEY",
        )
        self.assertIs(self.history.observe("x"), HistoryStatus.VALID_NEW)

    def test_unsafe_parsed_text_never_reaches_upstream_ast(self):
        with patch(
            "scripts.candidate_history.BooleanBenchmarkRefined._in_space",
            side_effect=AssertionError("Unexpected upstream AST construction"),
        ):
            self.assertIs(self.history.observe("Expression: x.__class__"), HistoryStatus.OUT_OF_SPACE)

    def test_original_preserves_backticks_left_by_the_official_parser(self):
        self.assertIs(self.history.observe("Expression: `x ∨ y`."), HistoryStatus.OUT_OF_SPACE)
        views = self.history.render(len)
        self.assertEqual(views.original.text, "Expression: `x OR y`")
        self.assertEqual(views.structured.text, "None\nDiagnostic: OUT_OF_SPACE")

    def test_reference_does_not_construct_a_labeled_benchmark(self):
        with patch(
            "scripts.candidate_history.BooleanBenchmarkRefined.__init__",
            side_effect=AssertionError("Unexpected upstream dataset load"),
        ):
            self.assertIs(self.new_history().observe("x"), HistoryStatus.VALID_NEW)

    def test_infrastructure_failure_does_not_update_either_view(self):
        self.history.observe("NOT y")
        before = self.history.render(len)
        self.assertIs(self.history.observe(None), HistoryStatus.INFRASTRUCTURE_FAILURE)
        self.assertEqual(self.history.render(len), before)

    def test_budget_drops_oldest_entries_whole_in_both_views(self):
        for expr in ("x", "y", "x OR y"):
            self.history.observe(expr)
        expected = "Expression: y\nExpression: x OR y"
        views = self.history.render(len, max_tokens=len(expected))
        self.assertEqual((views.original.text, views.structured.text), (expected, expected))
        self.assertEqual((views.original.token_count, views.structured.token_count), (len(expected),) * 2)
        self.assertEqual(self.history.render(len).structured.text, "Expression: x\n" + expected)

    def test_budget_counts_diagnostic_and_supports_diagnostics_ablation(self):
        self.history.observe("x")
        self.history.observe("NOT y")
        exact = "Expression: x"
        views = self.history.render(len, max_tokens=len(exact))
        self.assertEqual(views.structured.text, exact)
        self.assertLessEqual(views.original.token_count, len(exact))
        self.assertEqual(self.history.render(len, include_diagnostic=False).structured.text, exact)
        self.assertEqual(self.history.render(len, max_tokens=4).structured.text, "None")
        with self.assertRaisesRegex(ValueError, "empty history"):
            self.history.render(len, max_tokens=3)

    def test_token_counter_measures_rendered_block_not_characters(self):
        self.history.observe("x")
        self.history.observe("y")
        views = self.history.render(lambda text: len(text.split()), max_tokens=2)
        self.assertEqual((views.original.text, views.structured.text), ("Expression: y",) * 2)
        self.assertEqual((views.original.token_count, views.structured.token_count), (2, 2))

    def test_public_inputs_are_copied_and_trajectory_state_isolation(self):
        observation = BooleanObservation({"x": 1, "y": 0}, 1)
        left = CandidateHistory([observation])
        right = CandidateHistory([observation])
        observation.inputs["x"] = 0
        observation.output = 0
        self.assertIs(left.observe("y"), HistoryStatus.OBSERVATION_MISMATCH)
        self.assertIs(left.observe("x"), HistoryStatus.VALID_NEW)
        self.assertEqual(right.render(len).structured.text, "None")
        self.assertIs(right.observe("x"), HistoryStatus.VALID_NEW)

    def test_rejects_missing_or_nonpublic_observations(self):
        for rows in (
            [],
            [BooleanObservation({"x": 0, "y": 0, "extra": 1}, 0)],
            [BooleanObservation({"x": 0, "y": 0}, 2)],
            [{"inputs": {"x": 0, "y": 0}, "output": 0, "extra": []}],
        ):
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                CandidateHistory(rows)
