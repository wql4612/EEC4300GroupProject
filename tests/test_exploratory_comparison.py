import base64
import io
import json
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

import numpy as np

from scripts.candidate_history import BooleanObservation
from scripts.fscb_hypo import Bandit, Controller, structural_bucket
from scripts.run_exploratory import (
    collect, expected_slots, load_bundle, prepare, registered_manifest, score_records, verify_selection,
)


class ControllerTests(TestCase):
    def test_linu_cb_updates_only_the_selected_arm(self):
        bandit = Bandit("ACB", 42)
        context = np.array([1., 0.5, 1., 0., 0.])
        arm = bandit.choose(bandit.arms, context)
        bandit.update(arm, context, 1.)
        np.testing.assert_allclose(bandit.matrices[arm], np.eye(5) + np.outer(context, context))
        np.testing.assert_allclose(bandit.vectors[arm], context)
        self.assertEqual([bandit.counts[other] for other in bandit.arms if other != arm], [0, 0])

    def test_linu_cb_score_uses_context_after_warmup(self):
        bandit = Bandit("ACB", 42)
        preferred = bandit.arms[-1]
        for arm in bandit.arms:
            bandit.counts[arm] = 2
        bandit.vectors[preferred][1] = 1.
        self.assertEqual(bandit.choose(bandit.arms, np.array([1., .5, 1., 0., 0.])), preferred)

    def test_frontier_uses_actual_pre_update_bucket_and_skips_infrastructure(self):
        controller = Controller("FSCB", 42)
        controller.start_group([BooleanObservation({"x": 0, "y": 0}, 0)], seed=60)
        first = controller.decide(0)
        self.assertEqual(first.arm.mode, "standard")
        self.assertEqual(controller.observe(first, "Expression: x")["reward"], 1.)
        second = controller.decide(1)
        self.assertTrue(any(arm.mode == "frontier" for arm in second.available))
        failure = controller.observe(second, None)
        self.assertIsNone(failure["reward"])
        self.assertEqual(sum(controller.bandit.counts.values()), 1)
        third = controller.decide(2)
        actual = controller.observe(third, "Expression: y")
        self.assertAlmostEqual(actual["reward"], .75 + .25 * np.exp(-1))
        self.assertEqual(actual["actual_bucket"], "leaf")
        self.assertEqual(controller.coverage["leaf"], 2)
        controller.start_group([BooleanObservation({"x": 1, "y": 0}, 1)], seed=63)
        self.assertFalse(sum(controller.coverage.values()))
        self.assertEqual(sum(controller.bandit.counts.values()), 2)

    def test_frontier_misses_require_sent_frontier_and_target_novelty(self):
        controller = Controller("FSCB", 42)
        controller.start_group([BooleanObservation({"x": 0, "y": 0}, 0)], seed=42)
        controller.observe(controller.decide(0), "x")
        # Preheat Standard arms, forcing a newly available Frontier arm next.
        for arm in controller.bandit.arms:
            if arm.mode == "standard":
                controller.bandit.counts[arm] = 2
        decision = controller.decide(1)
        self.assertEqual(decision.arm.mode, "frontier")
        target = decision.target
        self.assertEqual(controller.observe(decision, "x")["frontier_hit"], False)
        self.assertEqual(controller.misses[target], 1)
        self.assertIsNone(controller.observe(decision, None)["frontier_hit"])
        self.assertEqual(controller.misses[target], 1)
        self.assertEqual(controller.observe(decision, "NOT x")["reward"], -.5)
        self.assertEqual(controller.misses[target], 2)
        self.assertNotEqual(controller.decide(2).target, target)

    def test_no_bonus_ablation_retains_frontier_and_restores_new_key_reward(self):
        controller = Controller("FSCB", 42, beta=0.)
        controller.start_group([BooleanObservation({"x": 0, "y": 0}, 0)], seed=42)
        controller.observe(controller.decide(0), "x")
        decision = controller.decide(1)
        self.assertTrue(any(arm.mode == "frontier" for arm in decision.available))
        self.assertEqual(controller.observe(decision, "y")["reward"], 1.)

    def test_all_five_structural_buckets(self):
        x = ("VAR", "x")
        y = ("VAR", "y")
        a = ("AND", (x, y))
        o = ("OR", (x, y))
        self.assertEqual(
            [structural_bucket(key) for key in (x, a, o, ("AND", (o, x)), ("OR", (a, x)))],
            ["leaf", "and_only", "or_only", "and_mixed", "or_mixed"],
        )


class ComparisonTests(TestCase):
    def test_frozen_manifest_has_12_pairs_and_96_matched_slots(self):
        manifest = registered_manifest()
        slots = list(expected_slots(manifest))
        self.assertEqual(len(slots), 96)
        self.assertEqual(len({(row["group_id"], row["seed"]) for row in slots}), 12)
        for a, b in zip(slots[::2], slots[1::2], strict=True):
            self.assertEqual({a["method"], b["method"]}, {"ACB", "FSCB"})
            self.assertEqual((a["group_id"], a["slot"], a["model_seed"]),
                             (b["group_id"], b["slot"], b["model_seed"]))

    def test_real_four_group_overlap_and_manifest_tamper_guards(self):
        self.assertEqual(len(verify_selection()), 4)
        with TemporaryDirectory() as temp:
            run = Path(temp) / "new"
            prepared = prepare(run)
            manifest, freeze_hash = load_bundle(run)
            self.assertEqual(freeze_hash, prepared["freeze_sha256"])
            manifest["blocks"][0]["ground_truth_expressions"] = []
            (run / "online_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "allowlisted plan"):
                load_bundle(run)

    def test_collection_smoke_under_production_file_lockdown(self):
        snippet = """
import io, socket, sys
socket.has_ipv6 = False
def no_network(event, args):
    if event.startswith('socket.') and event != 'socket.gethostname':
        raise AssertionError('Offline smoke attempted network access')
sys.addaudithook(no_network)
from scripts.run_exploratory import collect, registered_manifest, pinned, file_lockdown
m = registered_manifest()
m['blocks'] = [dict(group_id='synthetic', observations='(x=0, y=0) -> 0',
                    seed=42, slot_seeds=[42,43,44,45], first_method='ACB')]
log = io.StringIO()
sys.addaudithook(file_lockdown)
try:
    pinned.DATASET.read_bytes()
except PermissionError:
    pass
else:
    raise AssertionError('Answer read was not denied')
collect(m, log, lambda _: (200, b'{"response":"x","done":true}', None))
assert len(log.getvalue().splitlines()) == 8
"""
        subprocess.run([sys.executable, "-B", "-c", snippet], check=True, capture_output=True, text=True)

    def test_synthetic_smoke_uses_one_call_per_slot_and_official_replay(self):
        manifest = registered_manifest()
        # Synthetic rows, deliberately not one of the four held-out groups.
        group_id = "synthetic-smoke"
        manifest["blocks"] = [{
            "group_id": group_id, "observations": "(x=0, y=0) -> 0",
            "seed": 42, "slot_seeds": [42, 43, 44, 45], "first_method": "ACB",
        }]
        completed = lambda text: json.dumps({"done": True, "response": text}).encode()
        scripted = iter([(200, completed(expr), None) for expr in
                         ("x", "x", "y OR x", "y OR x", "x OR y", "x OR y", "", "")])
        calls = []

        def post(payload):
            calls.append(payload)
            return next(scripted)

        journal = io.StringIO()
        collect(manifest, journal, post)
        rows = [json.loads(line) for line in journal.getvalue().splitlines()]
        self.assertEqual(len(calls), 8)
        self.assertEqual(len(rows), 8)
        self.assertEqual(len(set(row["index"] for row in rows)), 8)
        self.assertEqual(base64.b64decode(rows[-1]["raw_body_b64"]), completed(""))
        labels = {group_id: {
            "observation_set_id": group_id, "n_observations": 1,
            "observations": [{"inputs": {"x": 0, "y": 0}, "output": 0}],
            "ground_truth_expressions": [
                {"truth_table": {}, "mechanistic_key": ("VAR", "x")},
                {"truth_table": {}, "mechanistic_key": ("OR", (("VAR", "x"), ("VAR", "y")))},
            ],
        }}
        result = score_records(manifest, rows, labels)
        for method in ("ACB", "FSCB"):
            self.assertEqual(result["summary"][method],
                             {"valid_rate": .75, "novelty_rate": .5, "recovery_rate": 1.})
        self.assertEqual(result["pairs"][0]["recovery_rate_delta"], 0.)
        rows[0]["request"]["options"]["temperature"] = .99
        with self.assertRaisesRegex(RuntimeError, "sampling parameters differ"):
            score_records(manifest, rows, labels)

    def test_infrastructure_failure_counts_as_slot_without_retry_or_feedback(self):
        manifest = registered_manifest()
        manifest["blocks"] = [{
            "group_id": "n1_inputs_00_outputs_0", "observations": "(x=0, y=0) -> 0",
            "seed": 42, "slot_seeds": [42], "first_method": "ACB",
        }]
        responses = iter([(None, b"", "synthetic timeout"), (200, b'{"done":true,"response":"x"}', None)])
        journal = io.StringIO()
        collect(manifest, journal, lambda payload: next(responses))
        rows = [json.loads(line) for line in journal.getvalue().splitlines()]
        self.assertEqual(len(rows), 2)
        self.assertIsNone(rows[0]["feedback"]["reward"])
        self.assertEqual(rows[1]["feedback"]["reward"], 1.)
