import hashlib
import json
import re
import subprocess
import sys
import unittest
from collections import Counter
from pathlib import Path

from scripts.run_boolean_smoke import is_safe_boolean_expression

ROOT = Path(__file__).resolve().parents[1]
UPSTREAM = ROOT.parent / "HypoSpace"
UPSTREAM_COMMIT = "c69e9318577b34b5b896996571aefd4ba6053f58"
PROMPTS = ROOT / "prompts" / "d2"
D1 = ROOT / "results" / "d1" / "smoke-retry-20261125-174050"
sys.path.insert(0, str(UPSTREAM / "boolean"))

from boolean_benchmark import BooleanBenchmarkRefined
from boolean_dataset import BooleanDiscoveryGame, BooleanObservation
from modules.llm_interface import LLMInterface

METRICS = ("parse_success_rate", "in_space_rate", "valid_rate", "novelty_rate", "recovery_rate")


class RawReplay(LLMInterface):
    def __init__(self, responses):
        self.responses = iter(responses)

    def get_name(self):
        return "d2-offline-preflight"

    def query(self, prompt):
        return next(self.responses)


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def render_template(template, observations, history):
    observations_block = "\n".join(observation.to_string() for observation in observations)
    history_block = "\n".join(f"Expression: {expression}" for expression in history) or "None"
    blocks = {"{{OBSERVATIONS}}": observations_block, "{{PRIOR_EXPRESSIONS}}": history_block}
    return re.sub(r"\{\{(?:OBSERVATIONS|PRIOR_EXPRESSIONS)\}\}", lambda match: blocks[match[0]], template)


class D2BooleanPreflightTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        commit = subprocess.check_output(["git", "-C", str(UPSTREAM), "rev-parse", "HEAD"], text=True).strip()
        if commit != UPSTREAM_COMMIT:
            raise AssertionError(f"Expected upstream {UPSTREAM_COMMIT}, got {commit}")
        cls.benchmark = BooleanBenchmarkRefined(str(ROOT / "data" / "d1" / "boolean_basic_depth2.json"))
        cls.d1_result = read_json(D1 / "result.json")["per_sample_results"][0]
        cls.d1_case = next(
            case for case in cls.benchmark.all_observation_sets
            if case["observation_set_id"] == cls.d1_result["observation_set_id"]
        )

    def evaluate(self, responses):
        replay = RawReplay(responses)
        result = self.benchmark.evaluate_single_observation_set(
            replay, self.d1_case, n_queries=len(responses), verbose=False, max_retries=1
        )
        self.assertIsNone(next(replay.responses, None), "Replay must consume exactly one raw response per slot")
        return result

    def assert_rates(self, result, expected):
        self.assertEqual(tuple(result[metric] for metric in METRICS), expected)

    def test_p0_renders_the_unmodified_official_prompt(self):
        template = (PROMPTS / "P0.txt").read_text(encoding="utf-8")
        self.assertEqual(template.count("{{OBSERVATIONS}}"), 1)
        self.assertEqual(template.count("{{PRIOR_EXPRESSIONS}}"), 1)
        observations = [BooleanObservation(obs["inputs"], obs["output"]) for obs in self.d1_case["observations"]]
        for history in ([], ["x", "NOT y", "x AND y"]):
            with self.subTest(history=history):
                self.assertEqual(
                    render_template(template, observations, history),
                    self.benchmark.create_prompt(observations, history),
                )

    def test_prompt_diffs_limit_which_blocks_change(self):
        p0, p1, p2 = [(PROMPTS / f"P{i}.txt").read_text(encoding="utf-8") for i in range(3)]
        replacements = (
            ("(x AND y) OR z ≠ x AND (y OR z)", "(x AND y) OR x ≠ x AND (y OR x)"),
            ("Expression: (x OR y) AND NOT x", "Expression: x OR y"),
            ("Expression: NOT (x AND y)", "Expression: x AND (x OR y)"),
            ("Expression: NOR(x, y)", "Expression: y OR (x AND y)"),
        )
        expected_p1 = p0
        for original, replacement in replacements:
            self.assertEqual(expected_p1.count(original), 1)
            expected_p1 = expected_p1.replace(original, replacement)
        self.assertEqual(p1, expected_p1)
        new_examples = """            Additional format examples:
            Expression: x AND y
            Expression: x OR y
"""
        new_rules = """            - For this basic grammar, AND and OR are the only allowed operators.
            - The examples illustrate only the syntax and output format. They do not represent the correct answers for the current observation group.
            - Your expression must match every observation for the current group.
"""
        self.assertEqual(p2.count(new_examples), 1)
        self.assertEqual(p2.count(new_rules), 1)
        self.assertEqual(p2.replace(new_examples, "", 1).replace(new_rules, "", 1), p1)

        for prompt, example_count in ((p1, 4), (p2, 6)):
            examples = re.findall(r"^ +Expression: (.+)$", prompt, flags=re.MULTILINE)
            self.assertEqual(len(examples), example_count)
            for expression in examples:
                with self.subTest(expression=expression):
                    self.assertTrue(self.benchmark._in_space(expression))
        for expression in ("(x AND y) OR x", "x AND (y OR x)"):
            self.assertTrue(self.benchmark._in_space(expression))
        self.assertNotEqual(
            self.benchmark.get_expression_mechanistic_key("(x AND y) OR x"),
            self.benchmark.get_expression_mechanistic_key("x AND (y OR x)"),
        )

    def test_prompt_hashes_match_review_manifest(self):
        manifest = (PROMPTS / "README.md").read_text(encoding="utf-8")
        for name in ("P0", "P1", "P2"):
            template = (PROMPTS / f"{name}.txt").read_text(encoding="utf-8")
            digest = hashlib.sha256(template.encode("utf-8")).hexdigest()
            self.assertIn(f"| {name} | `{digest}` |", manifest)

    def test_d1_negative_replays_the_archived_original_responses(self):
        journal = (D1 / "requests.jsonl").read_text(encoding="utf-8").splitlines()
        responses = [json.loads(json.loads(line)["raw_body"])["response"] for line in journal]
        result = self.evaluate(responses)
        self.assert_rates(result, (1, 0, 0, 0, 0))
        self.assert_rates(result, tuple(self.d1_result[metric] for metric in METRICS))
        self.assertEqual(result["all_hypotheses"], self.d1_result["all_hypotheses"])

    def test_d1_positive_replays_the_original_manual_fixture(self):
        saved = read_json(D1 / "manual-positive-control.json")["result"]
        result = self.evaluate(["Expression: x AND y", "Expression: y AND x"])
        self.assert_rates(result, (1, 1, 1, 0.5, 0.5))
        self.assert_rates(result, tuple(saved[metric] for metric in METRICS))
        self.assertEqual(result["unique_valid_expressions"], saved["unique_valid_expressions"])

    def test_single_valid_expression(self):
        self.assert_rates(self.evaluate(["Expression: x AND y"]), (1, 1, 1, 1, 0.5))

    def test_not_is_parseable_but_outside_basic_space(self):
        self.assert_rates(self.evaluate(["Expression: NOT x"]), (1, 0, 0, 0, 0))

    def test_ast_depth_three_is_outside_the_space(self):
        self.assert_rates(self.evaluate(["Expression: x OR (x AND (x OR y))"]), (1, 0, 0, 0, 0))

    def test_inconsistent_in_space_expression_still_counts_for_novelty(self):
        self.assert_rates(self.evaluate(["Expression: x OR y"]), (1, 1, 0, 1, 0))

    def test_unparseable_output_is_still_a_budget_slot(self):
        self.assert_rates(self.evaluate(["# no expression"]), (0, 0, 0, 0, 0))

    def test_reordered_duplicate_counts_once_for_novelty_and_recovery(self):
        result = self.evaluate(["Expression: y AND x", "Expression: x AND y"])
        self.assert_rates(result, (1, 1, 1, 0.5, 0.5))
        self.assertEqual(result["n_valid"], 2)
        self.assertEqual(result["n_unique_valid"], 1)

    def test_guard_rejection_leaves_the_raw_response_available_to_official_scoring(self):
        raw = "Expression: z"
        parsed = self.benchmark.parse_llm_response(raw)
        self.assertFalse(is_safe_boolean_expression(parsed))
        result = self.evaluate([raw])
        self.assert_rates(result, (1, 0, 0, 0, 0))
        self.assertEqual(result["all_hypotheses"], ["z"])

    def test_grammar_inventory_matches_the_documented_bucket_mapping(self):
        design = (ROOT / "docs" / "algorithm_design_v2.md").read_text(encoding="utf-8")
        documented_rows = re.findall(
            r"^\| `(\('(?:VAR|AND|OR)'.+\))` \| (leaf|AND-only|OR-only|AND-root mixed|OR-root mixed) \|$",
            design,
            flags=re.MULTILINE,
        )
        expected = dict(documented_rows)
        self.assertEqual(len(documented_rows), 10)
        self.assertEqual(len(expected), 10, "Each key must have exactly one row")

        operators = BooleanDiscoveryGame.OPERATOR_SETS["basic"]
        expressions = BooleanDiscoveryGame.generate_all_expressions(
            ["x", "y"], operators, 2, self.benchmark.mechanistic_opts
        )
        actual = {}
        for expression in expressions:
            key = expression.mechanistic_key(**self.benchmark.mechanistic_opts)
            root, children = key
            if root == "VAR":
                bucket = "leaf"
            elif any(child[0] in operators - {root} for child in children):
                bucket = f"{root}-root mixed"
            else:
                bucket = f"{root}-only"
            self.assertNotIn(repr(key), actual)
            actual[repr(key)] = bucket
            self.assertEqual(self.benchmark.get_expression_mechanistic_key(expression.formula), key)
        self.assertEqual(actual, expected)
        self.assertEqual(Counter(actual.values()), {
            "leaf": 2, "AND-only": 1, "OR-only": 1, "AND-root mixed": 3, "OR-root mixed": 3,
        })
