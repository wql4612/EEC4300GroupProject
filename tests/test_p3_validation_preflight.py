"""Exercise the P3 plan against the pinned evaluator with local fixtures only."""

import argparse
import base64
import csv
import json
import os
import re
import socket
import statistics
import subprocess
import sys
import tempfile
import unittest
from collections import defaultdict, deque
from contextlib import nullcontext, redirect_stdout
from copy import deepcopy
from io import StringIO
from pathlib import Path
from unittest.mock import patch


def deny_network(event, args):
    if (event.startswith("socket.") and event != "socket.gethostname") or event == "urllib.Request":
        raise AssertionError(f"Network access during offline preflight: {event}")


if __name__ == "__main__":
    # The child runs the real entry point with a mock transport. Skip urllib3's
    # optional IPv6 capability probe before importing the benchmark's dependencies.
    socket.has_ipv6 = False
    sys.addaudithook(deny_network)

from scripts import p3_collection_seal, p3_preflight as preflight
from scripts import prepare_p3_validation
from scripts import run_d2_prompt as collector
from scripts import score_d2_prompt as scorer
from scripts import score_p3_prompt
from test_d2_boolean_preflight import BooleanBenchmarkRefined, LLMInterface, ROOT, UPSTREAM, UPSTREAM_COMMIT

VARIANTS = ("P1", "P2", "P3")
PROMPTS = ROOT / "prompts/d2"
PLAN = ROOT / "docs/p3_validation_plan.md"
ANSWER_FILE = ROOT / "data/d1/boolean_basic_depth2.json"
BUNDLE_FILES = ("selection_manifest.json", "online_manifest.json", "freeze.json", "preflight.json")
MOCK_ENVIRONMENT = {
    "python_version": "3.11.15",
    "ollama": {"version": "0.40.14", "executable": str(ROOT / "ollama.exe"),
               "model_directory": str(ROOT / "mock-models"), "listener_pid": 4321,
               "listen_address": "127.0.0.1:11434"},
    "gpu": {"name": "NVIDIA GeForce RTX 4060 Laptop GPU", "total_mib": 8188, "free_mib": 5500},
    "log_directory": {"writable": True, "free_bytes": 100 * 1024 * 1024},
}


def observation_rows(group_id):
    inputs, outputs = re.fullmatch(r"n[1-3]_inputs_([01_]+)_outputs_([01]+)", group_id).groups()
    return [
        {"inputs": {"x": int(x), "y": int(y)}, "output": int(output)}
        for (x, y), output in zip(inputs.split("_"), outputs, strict=True)
    ]


def observation_text(group_id):
    return "\n".join(
        f"(x={row['inputs']['x']}, y={row['inputs']['y']}) -> {row['output']}"
        for row in observation_rows(group_id)
    )


def plan_fixture():
    plan = PLAN.read_text(encoding="utf-8")
    groups = {
        label: group_id
        for group_id, label in re.findall(r"^\| [123] \| \d \| `(n[^\x60]+)` \| (H[1-5])，", plan, re.M)
    }
    schedule = re.findall(r"^\| (\d+) \| (H[1-5]) \| ([\d /]+) \| (P[123] . P[123] . P[123]) \|$", plan, re.M)
    assert len(groups) == 5 and len(schedule) == 15
    templates = {name: (PROMPTS / f"{'P3-draft' if name == 'P3' else name}.txt").read_text(encoding="utf-8")
                 for name in VARIANTS}
    blocks = [
        {
            "id": f"{seed}-{label}", "group_id": groups[label], "seed": int(seed),
            "observations": observation_text(groups[label]),
            "slot_seeds": [int(n) for n in q_seeds.split(" / ")], "prompt_order": re.findall(r"P[123]", order),
        }
        for seed, label, q_seeds, order in schedule
    ]
    return {
        "model": "qwen2.5:3b",
        "model_digest": "357c53fb659c5076de1d65ccb0b397446227b71a42be9d1603d46168015c9e4b",
        "options": {"temperature": 0.6, "num_predict": 128, "num_ctx": 4096},
        "templates": templates, "blocks": blocks, "block_rerun_min_failures": 2,
    }


def mock_collector_process(run_dir, *, interrupted=False):
    """Run the actual collector entry point with a blocked network and fake responses."""
    file_reads = []
    denied_reads = []
    seal_events = []
    original_read_bytes = Path.read_bytes
    original_open = Path.open
    original_fsync = os.fsync
    original_make_receipt = p3_collection_seal.make_receipt

    def trace_fsync(fd):
        original_fsync(fd)
        seal_events.append(("fsync", call_count))

    def trace_make_receipt(data, bindings):
        receipt = original_make_receipt(data, bindings)
        seal_events.append(("receipt", call_count))
        return receipt

    def trace_read(path):
        file_reads.append(path.name)
        return original_read_bytes(path)

    def trace_open(path, *args, **kwargs):
        try:
            opened = original_open(path, *args, **kwargs)
            if path.name in {ANSWER_FILE.name, "selection_manifest.json", "official-results.json"}:
                opened.close()
                raise AssertionError(f"Collector directly opened {path.name}")
            return opened
        except PermissionError:
            denied_reads.append(path.name)
            raise

    completed = json.dumps({
        "done": True, "response": "Expression: x", "prompt_eval_count": 530, "eval_count": 5,
    }).encode()
    discarded = json.dumps({"done": True, "response": "Expression: y"}).encode()
    incomplete = json.dumps({"done": False, "response": "Expression: x"}).encode()
    call_count = 0

    def mock_post(payload):
        nonlocal call_count
        call_count += 1
        if interrupted and call_count == 2:
            raise RuntimeError("mock interruption")
        if call_count == 1:
            for path in (run_dir / "selection_manifest.json", run_dir.parent / "official-results.json"):
                try:
                    with path.open("rb"):
                        raise AssertionError(f"Collector could read {path.name}")
                except PermissionError:
                    pass
            return 503, b"busy", None
        if call_count == 2:
            return None, b"", "timeout"
        if call_count <= 12:
            return 200, discarded, None
        if call_count == 25:
            return 200, incomplete, None
        return 200, completed, None

    with (
        patch.object(Path, "read_bytes", trace_read),
        patch.object(Path, "open", trace_open),
        patch.object(os, "fsync", trace_fsync),
        patch.object(p3_collection_seal, "make_receipt", trace_make_receipt),
        patch.object(preflight, "probe_environment", return_value=MOCK_ENVIRONMENT),
        patch.object(collector, "post_once", mock_post),
        patch.object(sys, "argv", ["run_d2_prompt", str(run_dir), "--p3"]),
    ):
        collector.main()
    print(json.dumps({"reads": file_reads, "denied_reads": denied_reads, "mock_calls": call_count,
                      "seal_events": seal_events}))


class MockReplay(LLMInterface):
    def __init__(self, responses):
        self.responses = deque(responses)
        self.consumed = []

    def get_name(self):
        return "offline-mock-replay"

    def query(self, prompt):
        slot, raw = self.responses.popleft()
        self.consumed.append(slot)
        return raw


class P3ValidationPreflightTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        pin = subprocess.check_output(["git", "-C", str(UPSTREAM), "rev-parse", "HEAD"], text=True).strip()
        if pin != UPSTREAM_COMMIT:
            raise AssertionError(f"Expected upstream {UPSTREAM_COMMIT}, got {pin}")
        temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(temp.cleanup)
        cls.root = Path(temp.name)
        cls.run_dir = cls.root / "prepared"
        with (
            patch.object(preflight, "probe_environment", return_value=MOCK_ENVIRONMENT),
            patch.object(sys, "argv", ["prepare_p3_validation", str(cls.run_dir)]),
            patch.object(collector, "post_once", side_effect=AssertionError("Prepare tried generation")),
        ):
            prepare_p3_validation.main()
        cls.manifest = json.loads((cls.run_dir / "online_manifest.json").read_bytes())
        cls.freeze = json.loads((cls.run_dir / "freeze.json").read_bytes())
        cls.case = {
            "observation_set_id": "mock-scoring-only", "n_observations": 1,
            "observations": [{"inputs": {"x": 1, "y": 1}, "output": 1}],
            "ground_truth_expressions": [
                {"mechanistic_key": "('VAR', 'x')", "truth_table": {}},
                {"mechanistic_key": "('VAR', 'y')", "truth_table": {}},
            ],
        }
        datasets = defaultdict(list, {"1": [cls.case]})
        for group_id in sorted({block["group_id"] for block in cls.manifest["blocks"]}):
            observations = observation_rows(group_id)
            datasets[str(len(observations))].append({
                **cls.case, "observation_set_id": group_id,
                "observations": observations, "n_observations": len(observations),
            })
        cls.dataset = cls.root / "mock_answers.json"
        scorer.document(cls.dataset, {
            "metadata": {
                "variables": ["x", "y"], "operators": ["AND", "OR"], "max_depth": 2,
                "mechanistic_opts": {"apply_commutativity": True, "apply_idempotence_and_or": True,
                                     "flatten_associativity": True},
            },
            "datasets_by_n_observations": datasets,
        })
        cls.benchmark = BooleanBenchmarkRefined(str(cls.dataset))
        scorer.document(cls.root / "official-results.json", {"offline_only": "answer sentinel"})
        demo = subprocess.check_output(
            [sys.executable, "-B", "-m", "test_p3_validation_preflight", str(cls.run_dir)],
            cwd=ROOT, env={**os.environ, "PYTHONPATH": str(ROOT / "tests")},
            stderr=subprocess.STDOUT, encoding="utf-8",
        )
        cls.collector_trace = json.loads(demo.splitlines()[-1])
        cls.archived, cls.selected, cls.reruns = scorer.checked_journal(
            cls.run_dir, cls.manifest, cls.freeze, variants=VARIANTS, selection_file="selection_manifest.json",
        )

    def evaluate(self, replay, slots, max_retries=1):
        with (
            patch("http.client.HTTPConnection", side_effect=AssertionError("Replay used HTTP")),
            patch("socket.create_connection", side_effect=AssertionError("Replay used a socket")),
            patch("requests.sessions.Session.request", side_effect=AssertionError("Replay used requests")),
        ):
            return self.benchmark.evaluate_single_observation_set(
                replay, self.case, n_queries=slots, verbose=False, max_retries=max_retries
            )

    def test_official_one_attempt_consumes_success_unparseable_and_error_slots(self):
        responses = ["Expression: x", "# no expression", "Error querying Ollama: timeout", "Expression: y"]
        replay = MockReplay(enumerate(responses, 1))
        result = self.evaluate(replay, 4)
        self.assertEqual(replay.consumed, [1, 2, 3, 4])
        self.assertFalse(replay.responses)
        self.assertEqual((result["valid_rate"], result["recovery_rate"]), (0.5, 1.0))
        self.assertEqual([(e["query_index"], e["attempt"]) for e in result["errors"]], [(2, 1)])

    def test_official_two_attempt_control_consumes_extra_response_after_failure(self):
        for first in ("# no expression", "Error querying Ollama: timeout"):
            with self.subTest(first=first):
                replay = MockReplay(enumerate([first, "Expression: x", "Expression: y"], 1))
                result = self.evaluate(replay, 2, max_retries=2)
                self.assertEqual(replay.consumed, [1, 2, 3])
                self.assertEqual(result["n_queries"], 2)

    def test_rerun_selection_preserves_all_attempts_and_keeps_single_failure(self):
        self.assertEqual((len(self.archived), len(self.selected), self.reruns), (192, 180, 1))
        first_block = self.manifest["blocks"][0]["id"]
        first_archive = [row for row in self.archived if row["block"] == first_block]
        first_selected = [row for row in self.selected if row["block"] == first_block]
        self.assertEqual([row["attempt"] for row in first_archive], [1] * 12 + [2] * 12)
        self.assertEqual([row["attempt"] for row in first_selected], [2] * 12)
        self.assertEqual(sum(bool(row["infrastructure_error"]) for row in self.selected), 1)
        self.assertEqual(len((self.run_dir / "requests.jsonl").read_text(encoding="utf-8").splitlines()), 192)

    def test_partial_response_does_not_enter_the_next_prompt_history(self):
        failed = next(row for row in self.selected if row["infrastructure_error"])
        self.assertEqual(failed["response"], "Expression: x")
        self.assertIsNone(failed["parsed_expression"])
        self.assertFalse(failed["guard_pass"])
        later = next(
            row for row in self.selected
            if (row["block"], row["variant"], row["slot"]) == (failed["block"], failed["variant"], failed["slot"] + 1)
        )
        block = next(block for block in self.manifest["blocks"] if block["id"] == failed["block"])
        expected_prompt = collector.render(self.manifest["templates"][failed["variant"]], block["observations"], [])
        self.assertEqual(later["request"]["prompt"], expected_prompt)

    def test_all_planned_slots_match_official_scoring_and_vr_denominators(self):
        trajectories = defaultdict(list)
        for row in self.selected:
            trajectories[row["block"], row["variant"]].append(row)
        self.assertEqual(len(trajectories), 45)
        scored = []
        results = defaultdict(list)
        for (block, variant), rows in trajectories.items():
            slot_ids = [(block, variant, row["slot"]) for row in rows]
            self.assertEqual([row["slot"] for row in rows], [1, 2, 3, 4])
            replay = MockReplay([
                (slot, scorer.replay_text(row))
                for slot, row in zip(slot_ids, rows, strict=True)
            ])
            result = self.evaluate(replay, 4)
            self.assertEqual(replay.consumed, slot_ids)
            self.assertFalse(replay.responses)
            self.assertEqual(result["n_queries"], 4)
            self.assertEqual(result["n_valid"], sum(not row["infrastructure_error"] for row in rows))
            self.assertEqual(result["recovery_rate"], 0.5)
            scored.extend(replay.consumed)
            results[variant].append(result)
        self.assertEqual(len(scored), 180)
        self.assertEqual(set(scored), {(row["block"], row["variant"], row["slot"]) for row in self.selected})
        for runs in results.values():
            self.assertEqual(len(runs), 15)
            self.assertEqual(sum(run["n_queries"] for run in runs), 60)
            self.assertAlmostEqual(sum(run["n_valid"] for run in runs) / 60,
                                   statistics.mean(run["valid_rate"] for run in runs))

    def test_collector_reads_only_manifest_receipt_and_own_script(self):
        self.assertEqual(self.collector_trace["reads"],
                         ["online_manifest.json", "freeze.json", "preflight.json"])
        self.assertEqual(self.collector_trace["denied_reads"],
                         [ANSWER_FILE.name, "selection_manifest.json", "official-results.json"])
        self.assertEqual(self.collector_trace["mock_calls"], 192)

    def test_token_counts_and_elapsed_time_survive_archiving(self):
        for row in self.selected:
            self.assertGreaterEqual(row["elapsed_seconds"], 0)
            body = json.loads(base64.b64decode(row["raw_body_b64"], validate=True))
            if row["infrastructure_error"]:
                self.assertNotIn("prompt_eval_count", body)
                self.assertNotIn("eval_count", body)
            else:
                self.assertEqual((body["prompt_eval_count"], body["eval_count"]), (530, 5))

    def copy_bundle(self, target, *, journal=False):
        for name in BUNDLE_FILES + (("requests.jsonl", p3_collection_seal.SEAL) if journal else ()):
            (target / name).write_bytes((self.run_dir / name).read_bytes())

    def seal_fixture(self, target):
        _, _, bindings = preflight.read_bundle(target, online=False)
        data = (target / "requests.jsonl").read_bytes()
        receipt = p3_collection_seal.make_receipt(data, bindings)
        (target / p3_collection_seal.SEAL).write_bytes(preflight.json_bytes(receipt))

    def assert_production_rejects(self, config, expected_error, *, stale=False, alter_freeze=None,
                                  alter_receipt=None, alter_files=None, environment=MOCK_ENVIRONMENT):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            self.copy_bundle(target)
            config_bytes = preflight.json_bytes(config)
            (target / "online_manifest.json").write_bytes(config_bytes)
            freeze = {**deepcopy(self.freeze), "online_manifest_sha256": collector.sha256(config_bytes)}
            if alter_freeze:
                alter_freeze(freeze)
            freeze_bytes = preflight.json_bytes(freeze)
            (target / "freeze.json").write_bytes(freeze_bytes)
            receipt = json.loads((target / "preflight.json").read_bytes())
            receipt.update({field: freeze[field] for field in preflight.COMMON_FIELDS & freeze.keys()})
            receipt["freeze_sha256"] = collector.sha256(freeze_bytes)
            if stale:
                receipt["online_manifest_sha256"] = "0" * 64
            if alter_receipt:
                alter_receipt(receipt)
            (target / "preflight.json").write_bytes(preflight.json_bytes(receipt))
            if alter_files:
                alter_files(target)
            with (
                patch.object(sys, "argv", ["run_d2_prompt", str(target), "--p3"]),
                patch.object(preflight, "probe_environment", return_value=environment) if environment is not None else nullcontext(),
                patch.object(collector, "post_once", side_effect=AssertionError("Rejected plan attempted HTTP")) as post,
                self.assertRaisesRegex((RuntimeError, SystemExit), expected_error),
            ):
                collector.main()
            post.assert_not_called()
            self.assertFalse((target / "requests.jsonl").exists())

    def test_production_rejects_ground_truth_and_offline_fields_before_opening_journal(self):
        for field in ("ground_truth_expressions", "n_ground_truths", "mechanistic_key",
                      "compatible_expressions", "official_results"):
            with self.subTest(field=field):
                self.assert_production_rejects({**self.manifest, field: "sentinel"}, "manifest fields")
                first, *others = self.manifest["blocks"]
                changed = {**self.manifest, "blocks": [{**first, field: "sentinel"}, *others]}
                self.assert_production_rejects(changed, "Block fields")

    def test_production_rejects_wrong_group_observations_and_slot_count(self):
        first, *others = self.manifest["blocks"]
        for field, value, error in (
            ("group_id", "n1_inputs_01_outputs_1", "Group ID"),
            ("observations", "private sentinel", "Observations"),
            ("slot_seeds", first["slot_seeds"][:-1], "180|[Ss]lot"),
            ("slot_seeds", [1, 2, 3, 4], "seed"),
            ("prompt_order", ["P1", "P2", "P0"], "Prompt"),
        ):
            with self.subTest(field=field, value=value):
                changed = {**self.manifest, "blocks": [{**first, field: value}, *others]}
                self.assert_production_rejects(changed, error)
        alternative = "n1_inputs_01_outputs_1"
        changed = {**self.manifest, "blocks": [
            {**first, "group_id": alternative, "observations": observation_text(alternative)}, *others,
        ]}
        self.assert_production_rejects(changed, "Group ID differs from the frozen plan")

    def test_production_rejects_stale_preflight_and_wrong_frozen_budget(self):
        self.assert_production_rejects(self.manifest, "[Pp]reflight.*match", stale=True)
        self.assert_production_rejects(
            self.manifest, "180", alter_freeze=lambda freeze: freeze.update(planned_requests=179),
        )
        self.assert_production_rejects(
            self.manifest, "Freeze fields", alter_freeze=lambda freeze: freeze.update(ground_truth_expressions=["sentinel"]),
        )

    def test_prepare_cli_generates_deterministic_plan_matching_the_preregistration(self):
        self.assertEqual(self.manifest, plan_fixture())
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "again"
            with patch.object(preflight, "probe_environment", return_value=MOCK_ENVIRONMENT):
                prepare_p3_validation.prepare(target)
            self.assertEqual({path.name for path in target.iterdir()}, set(BUNDLE_FILES))
            for name in BUNDLE_FILES:
                with self.subTest(file=name):
                    self.assertEqual((target / name).read_bytes(), (self.run_dir / name).read_bytes())
        self.assertEqual((len(self.archived), len(self.selected), len(self.manifest["blocks"])), (192, 180, 15))

    def test_prepare_and_collection_refuse_an_existing_output_and_preserve_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            self.copy_bundle(target)
            result_path = target / "official-results.json"
            result_path.write_bytes(b"previous result")
            before = {path.name: path.read_bytes() for path in target.iterdir()}
            with self.assertRaisesRegex(FileExistsError, "Refusing to overwrite"):
                prepare_p3_validation.prepare(target)
            with (
                patch.object(sys, "argv", ["run_d2_prompt", str(target), "--p3"]),
                patch.object(preflight, "probe_environment", return_value=MOCK_ENVIRONMENT),
                patch.object(collector, "post_once") as post,
                self.assertRaisesRegex(RuntimeError, "already contains"),
            ):
                collector.main()
            post.assert_not_called()
            with (
                patch.object(sys, "argv", ["score_p3_prompt", str(target)]),
                self.assertRaisesRegex(FileExistsError, "already exist"),
            ):
                score_p3_prompt.main()
            self.assertEqual({path.name: path.read_bytes() for path in target.iterdir()}, before)

    def test_every_freeze_field_is_required_before_first_request(self):
        for field in sorted(preflight.FREEZE_FIELDS):
            with self.subTest(field=field):
                self.assert_production_rejects(
                    self.manifest, f"missing=.*{field}", alter_freeze=lambda freeze: freeze.pop(field),
                )

    def test_freeze_rejects_nested_unknown_missing_and_wrong_types(self):
        for section, field, value in (
            ("model", "unknown", True), ("model", "digest", False), ("model", "parameters", {}),
            ("options", "temperature", "0.6"), ("options", "num_ctx", 4096.0),
            ("prompt_hashes", "P3", 3), ("prompt_hashes", "P0", "0" * 64),
        ):
            with self.subTest(section=section, field=field):
                self.assert_production_rejects(
                    self.manifest, rf"Freeze\.{section}",
                    alter_freeze=lambda freeze: freeze[section].update({field: value}),
                )
        self.assert_production_rejects(
            self.manifest, "missing=.*template_sha256", alter_freeze=lambda freeze: freeze["model"].pop("template_sha256"),
        )

    def test_freeze_rejects_hash_format_source_mismatch_wrong_seeds_and_model(self):
        for field, value, error in (
            ("collector_sha256", "q" * 64, "64-digit SHA-256"),
            ("collector_sha256", "1" * 64, "Actual collector_sha256"),
            ("verifier_sha256", "0" * 64, "Actual verifier_sha256"),
            ("online_manifest_sha256", "1" * 64, "Actual online_manifest_sha256"),
            ("selection_manifest_sha256", "0" * 64, "registered selection"),
            ("dataset_sha256", "0" * 64, "registered dataset"),
            ("rollout_seeds", [42, 271828, 271828], "registered seeds"),
            ("rollout_seeds", [42.0, 271828, 314159], "registered seeds"),
            ("planned_requests", 180.0, "must be 180"),
            ("upstream_commit", "0" * 40, "registered HypoSpace commit"),
            ("model", {**self.freeze["model"], "digest": "0" * 64}, "registered name, digest"),
        ):
            with self.subTest(field=field, value=value):
                self.assert_production_rejects(
                    self.manifest, error, alter_freeze=lambda freeze: freeze.update({field: value}),
                )

    def test_every_preflight_field_and_check_is_required_before_first_request(self):
        for field in sorted(preflight.PREFLIGHT_FIELDS):
            with self.subTest(field=field):
                self.assert_production_rejects(
                    self.manifest, f"missing=.*{field}", alter_receipt=lambda receipt: receipt.pop(field),
                )
        for check in preflight.REQUIRED_CHECKS:
            with self.subTest(check=check):
                self.assert_production_rejects(
                    self.manifest, f"missing=.*{check}", alter_receipt=lambda receipt: receipt["checks"].pop(check),
                )
                self.assert_production_rejects(
                    self.manifest, f"did not pass: {check}",
                    alter_receipt=lambda receipt: receipt["checks"].update({check: False}),
                )
        self.assert_production_rejects(
            self.manifest, "Preflight fields.*unknown",
            alter_receipt=lambda receipt: receipt.update(ground_truth_expressions=[]),
        )

    def test_preflight_rejects_mismatched_hashes_and_tampered_offline_files(self):
        for field in sorted(preflight.FROZEN_HASH_FIELDS | {"freeze_sha256"}):
            with self.subTest(field=field):
                self.assert_production_rejects(
                    self.manifest, field, alter_receipt=lambda receipt: receipt.update({field: "f" * 64}),
                )
        self.assert_production_rejects(
            self.manifest, "Actual selection_manifest_sha256",
            alter_files=lambda target: (target / preflight.SELECTION_FILE).write_bytes(b"modified"),
        )
        with tempfile.TemporaryDirectory() as directory:
            changed = Path(directory) / "modified_answers.json"
            changed.write_bytes(b"modified")
            with patch.object(preflight, "DATASET", changed):
                self.assert_production_rejects(self.manifest, "Actual dataset_sha256")

    def test_preflight_rejects_wrong_current_upstream_commit_and_dirty_checkout(self):
        with patch.object(preflight, "local_command", return_value=f"{'a' * 40}\n"):
            self.assert_production_rejects(self.manifest, "HypoSpace HEAD")
        with patch.object(preflight, "local_command", side_effect=[preflight.UPSTREAM_COMMIT, "?? unexpected"]):
            self.assert_production_rejects(self.manifest, "checkout must be clean")

    def test_public_json_rejects_duplicate_keys_before_first_request(self):
        self.assert_production_rejects(
            self.manifest, "duplicate field.*model",
            alter_files=lambda target: (target / "online_manifest.json").write_bytes(
                b'{"model":"first","model":"second"}',
            ),
        )

    def test_preflight_rechecks_gpu_memory_python_disk_and_writability(self):
        for section, field, value, error in (
            ("gpu", "name", "NVIDIA GeForce RTX 3060", "RTX 4060"),
            ("gpu", "total_mib", 6000, "8 GiB"),
            ("gpu", "free_mib", 3000, "free VRAM"),
            ("log_directory", "writable", False, "not writable"),
            ("log_directory", "free_bytes", 100, "free bytes"),
        ):
            with self.subTest(field=field):
                environment = deepcopy(MOCK_ENVIRONMENT)
                environment[section][field] = value
                self.assert_production_rejects(self.manifest, error, environment=environment)
        for version, error in (("3.10.7", "Python 3.11"), ("3.11.14", "differs from Preflight")):
            with self.subTest(version=version):
                self.assert_production_rejects(
                    self.manifest, error, environment={**MOCK_ENVIRONMENT, "python_version": version},
                )

    @unittest.skipUnless(sys.platform == "win32", "Windows local runtime metadata probe")
    def test_preflight_rejects_actual_runtime_version_and_model_manifest_mismatch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            executable = root / "ollama.exe"
            with patch.object(preflight, "windows_ollama_installation", return_value=(executable, "0.40.18", root)):
                self.assert_production_rejects(self.manifest, "Installed Ollama version", environment=None)
            bad_manifest = root / "manifests/registry.ollama.ai/library/qwen2.5/3b"
            bad_manifest.parent.mkdir(parents=True)
            bad_manifest.write_bytes(b"unregistered model")
            with (
                patch.object(preflight, "windows_ollama_installation",
                             return_value=(executable, preflight.OLLAMA_VERSION, root)),
                patch.object(preflight, "verify_ollama_runtime"),
            ):
                self.assert_production_rejects(self.manifest, "Local model digest", environment=None)

    @unittest.skipUnless(sys.platform == "win32", "Windows local listener probe")
    def test_listener_check_binds_loopback_to_the_registered_binary_without_http(self):
        executable = self.root / "ollama.exe"
        inventory = ("TCP 127.0.0.1:11434 0.0.0.0:0 LISTENING 4321\n"
                     "TCP 127.0.0.1:11434 127.0.0.1:50714 ESTABLISHED 4321\n")
        with patch.object(preflight, "local_command", side_effect=[inventory, str(executable)]) as command:
            preflight.verify_ollama_listener(executable)
        self.assertEqual(command.call_args_list[0].args, ("netstat.exe", "-ano"))
        self.assertIn("4321", command.call_args_list[1].args[-1])
        with (
            patch.object(preflight, "local_command", side_effect=[inventory, str(self.root / "other.exe")]),
            self.assertRaisesRegex(RuntimeError, "does not belong"),
        ):
            preflight.verify_ollama_listener(executable)

    def test_listener_rejects_ipv4_ipv6_and_other_non_loopback_bindings(self):
        loopback = "TCP 127.0.0.1:11434 0.0.0.0:0 LISTENING 4321\n"
        for listing in (
            "TCP 0.0.0.0:11434 0.0.0.0:0 LISTENING 4321",
            loopback + "TCP [::]:11434 [::]:0 LISTENING 4321",
            loopback + "TCP 192.168.1.4:8030 0.0.0.0:0 LISTENING 4321",
        ):
            with (
                self.subTest(listing=listing),
                patch.object(preflight, "local_command", return_value=listing),
                self.assertRaisesRegex(RuntimeError, "loopback|exclusively"),
            ):
                preflight.verify_ollama_listener(self.root / "ollama.exe")

    def test_cli_version_uses_only_a_non_listening_port_and_disables_proxies(self):
        output = "Warning: could not connect to a running Ollama instance\nWarning: client version is 0.40.14\n"
        executable = self.root / "ollama.exe"
        with (
            patch.dict(os.environ, {"HTTPS_PROXY": "http://invalid", "ALL_PROXY": "http://invalid"}),
            patch.object(preflight, "local_command", return_value=output) as command,
        ):
            self.assertEqual(preflight.inspect_ollama_cli_version(executable), "0.40.14")
        self.assertEqual(command.call_args.args, (str(executable), "--version"))
        env = command.call_args.kwargs["env"]
        self.assertEqual((env["OLLAMA_HOST"], env["NO_PROXY"]), ("http://127.0.0.1:0", "*"))
        self.assertNotIn("HTTPS_PROXY", env)
        self.assertNotIn("ALL_PROXY", env)

    def test_runtime_rejects_cli_registry_disagreement_and_live_model_override(self):
        exe = self.root / "ollama.exe"
        models = self.root / "registered-models"
        live = {"OLLAMA_MODELS": str(models), "OLLAMA_HOST": "127.0.0.1:11434"}
        with (
            patch.object(preflight, "inspect_ollama_cli_version", return_value="0.40.18"),
            self.assertRaisesRegex(RuntimeError, "CLI version differs"),
        ):
            preflight.verify_ollama_runtime(exe, "0.40.14", models)
        with (
            patch.object(preflight, "inspect_ollama_cli_version", return_value="0.40.14"),
            patch.object(preflight, "verify_ollama_listener", return_value=4321),
            patch.object(preflight, "read_service_environment", return_value=live) as read,
        ):
            self.assertEqual(preflight.verify_ollama_runtime(exe, "0.40.14", models)["listener_pid"], 4321)
            read.assert_called_once_with(4321)
            live["OLLAMA_MODELS"] = str(self.root / "other-models")
            with self.assertRaisesRegex(RuntimeError, "model directory differs"):
                preflight.verify_ollama_runtime(exe, "0.40.14", models)
            live["OLLAMA_MODELS"] = str(models)
            live["OLLAMA_HOST"] = "0.0.0.0:11434"
            with self.assertRaisesRegex(RuntimeError, "configured for IPv4 loopback"):
                preflight.verify_ollama_runtime(exe, "0.40.14", models)

    def test_blob_sha_detects_equal_size_weights_and_unreferenced_blob_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            blobs = root / "blobs"
            blobs.mkdir()
            manifest = root / "manifests/registry.ollama.ai/library/qwen2.5/3b"
            manifest.parent.mkdir(parents=True)

            def blob(kind, contents):
                digest = collector.sha256(contents)
                (blobs / f"sha256-{digest}").write_bytes(contents)
                return {"mediaType": kind, "size": len(contents), "digest": f"sha256:{digest}"}

            weight = blob("application/vnd.ollama.image.model", b"weights")
            template = blob("application/vnd.ollama.image.template", b"template")
            extra = blob("extra", b"stored")
            raw = preflight.json_bytes({"config": blob("configuration", b"config"), "layers": [weight, template]})
            manifest.write_bytes(raw)
            with (
                patch.object(collector, "MODEL_DIGEST", collector.sha256(raw)),
                patch.object(preflight, "TEMPLATE_SHA256", template["digest"].split(":")[1]),
            ):
                preflight.verify_local_model(root)
                weights = blobs / weight["digest"].replace(":", "-")
                weights.write_bytes(b"tamper!")
                with self.assertRaisesRegex(RuntimeError, "blob hash differs: .*image.model"):
                    preflight.verify_local_model(root)
                weights.write_bytes(b"weights")
                (blobs / extra["digest"].replace(":", "-")).write_bytes(b"tamper")
                with self.assertRaisesRegex(RuntimeError, "blob hash differs: sha256-"):
                    preflight.verify_local_model(root)

    def test_freeze_binds_scorers_and_local_dependencies_at_both_entrypoints(self):
        for path in preflight.LOCAL_SOURCE_FILES:
            with self.subTest(path=path):
                self.assert_production_rejects(
                    self.manifest, "local_source_hashes.*missing",
                    alter_freeze=lambda freeze: freeze["local_source_hashes"].pop(path),
                )
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            self.copy_bundle(target, journal=True)
            hashes = preflight.source_hashes()
            hashes["local_source_hashes"]["scripts/score_p3_prompt.py"] = "0" * 64
            with (
                patch.object(preflight, "source_hashes", return_value=hashes),
                patch.object(sys, "argv", ["score_p3_prompt", str(target)]),
                patch.object(scorer, "checked_journal") as replay,
                self.assertRaisesRegex(RuntimeError, "Actual local_source_hashes"),
            ):
                score_p3_prompt.main()
            replay.assert_not_called()

    def test_freeze_rejects_scoring_runtime_version_drift_before_collection(self):
        self.assert_production_rejects(
            self.manifest, "scoring dependency versions differ",
            alter_freeze=lambda freeze: freeze["scoring_versions"].update(sympy="0.0"),
        )

    def test_scoring_rejects_broken_seals_and_source_inputs_before_reading_journal(self):
        for filename, mutation, error in (
            ("freeze.json", lambda data: data.pop("options"), "Freeze fields"),
            ("preflight.json", lambda data: data.pop("model_digest"), "Preflight fields"),
            ("online_manifest.json", None, "Actual online_manifest_sha256"),
            ("selection_manifest.json", None, "Actual selection_manifest_sha256"),
        ):
            with self.subTest(filename=filename), tempfile.TemporaryDirectory() as directory:
                target = Path(directory)
                self.copy_bundle(target)
                path = target / filename
                if mutation:
                    value = json.loads(path.read_bytes())
                    mutation(value)
                    path.write_bytes(preflight.json_bytes(value))
                else:
                    path.write_bytes(path.read_bytes() + b"\n")
                with (
                    patch.object(sys, "argv", ["score_p3_prompt", str(target)]),
                    patch.object(scorer, "checked_journal") as journal,
                    self.assertRaisesRegex(RuntimeError, error),
                ):
                    score_p3_prompt.main()
                journal.assert_not_called()

    def test_d2_offline_replay_matches_archived_45_tracks_and_180_slots(self):
        historical = ROOT / "results/d2/prompt-20261126"
        before = {path.name: preflight.digest_file(path) for path in historical.iterdir() if path.is_file()}
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            for name in ("online_manifest.json", "freeze.json", "preflight.json", "split_manifest.json",
                         "requests.jsonl", "reviewed-replay-expressions.json"):
                (target / name).write_bytes((historical / name).read_bytes())
            with patch.object(sys, "argv", ["score_d2_prompt", str(target)]), redirect_stdout(StringIO()):
                scorer.main()
            old = json.loads((historical / "official-results.json").read_bytes())
            new = json.loads((target / "official-results.json").read_bytes())

            def compare(original, replay):
                if isinstance(original, dict):
                    for key, value in original.items():
                        compare(value, replay[key])
                elif isinstance(original, list):
                    self.assertEqual(len(original), len(replay))
                    for original_item, replay_item in zip(original, replay, strict=True):
                        compare(original_item, replay_item)
                else:
                    self.assertEqual(original, replay)

            self.assertEqual((len(new["by_group_seed"]), len(new["by_slot"])), (45, 180))
            compare(old, new)
            with (
                (historical / "group-seed-metrics.csv").open(encoding="utf-8", newline="") as old_csv,
                (target / "group-seed-metrics.csv").open(encoding="utf-8", newline="") as new_csv,
            ):
                compare(list(csv.DictReader(old_csv)), list(csv.DictReader(new_csv)))
        self.assertEqual({path.name: preflight.digest_file(path) for path in historical.iterdir() if path.is_file()}, before)

    def test_p3_entry_scores_180_selected_slots_and_leaves_raw_archive_untouched(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            self.copy_bundle(target, journal=True)
            journal = target / "requests.jsonl"
            before = journal.read_bytes()
            with (
                patch.object(sys, "argv", ["score_p3_prompt", str(target)]),
                patch("http.client.HTTPConnection", side_effect=AssertionError("Scorer attempted HTTP")),
                redirect_stdout(StringIO()),
            ):
                score_p3_prompt.main()
            result = json.loads((target / "official-results.json").read_bytes())
            self.assertEqual(journal.read_bytes(), before)
            self.assertEqual(result["collection_seal"]["requests_sha256"], collector.sha256(before))
            self.assertEqual(result["collection_seal"]["actual_requests"], 192)
        self.assertEqual(set(result["summary"]), set(VARIANTS))
        self.assertEqual((result["planned_requests"], result["rerun_blocks"]), (180, 1))
        self.assertEqual(len(result["by_group_seed"]), 45)
        self.assertEqual(len(result["by_slot"]), 180)
        failed = [row for row in result["by_slot"] if row["infrastructure_error"]]
        self.assertEqual(len(failed), 1)
        self.assertFalse(failed[0]["official_valid"])
        self.assertIsNone(failed[0]["parsed"])
        archived_failure = next(row for row in self.selected if row["infrastructure_error"])
        self.assertEqual(archived_failure["response"], "Expression: x")
        for variant, summary in result["summary"].items():
            self.assertIn("ast_depth_violation_rate", summary["diagnostics"])
            self.assertIn("duplicate_rate", summary["diagnostics"])
            selected = summary["request_cost"]["selected_attempts"]
            all_attempts = summary["request_cost"]["all_attempts"]
            completed = 59 if variant == "P2" else 60
            self.assertEqual((selected["requests"], selected["timing_valid_records"]), (60, 60))
            self.assertEqual((all_attempts["requests"], all_attempts["timing_valid_records"]), (64, 64))
            self.assertEqual((selected["input_tokens"], selected["output_tokens"]), (530 * completed, 5 * completed))
            self.assertEqual(selected["token_missing_records"], 60 - completed)
            self.assertEqual(all_attempts["token_missing_records"], 64 - completed)

    def test_p3_rejects_d2_conditions_before_opening_the_journal(self):
        original = self.manifest["templates"]
        wrong = {**self.manifest, "templates": {"P0": original["P3"], "P1": original["P1"], "P2": original["P2"]}}
        with self.assertRaisesRegex(RuntimeError, "condition"):
            scorer.checked_journal(
                self.run_dir, wrong, self.freeze, variants=VARIANTS, selection_file="selection_manifest.json",
            )

    def test_sealed_body_drives_failure_mapping_and_completed_text_is_verbatim(self):
        text = " \nExpression: x\n  "
        cases = (
            ("normal", 200, {"done": True, "response": text}, None, text, True),
            ("empty model text", 200, {"done": True, "response": ""}, None, "", True),
            ("empty HTTP body", 200, b"", None, None, False),
            ("malformed JSON", 200, b'{"response":', None, None, False),
            ("HTTP error", 503, {"done": True, "response": text}, None, text, False),
            ("partial generation", 200, {"done": False, "response": text}, None, text, False),
            ("transport error", 200, {"done": True, "response": text}, "IncompleteRead", text, False),
            ("non-object JSON", 200, [], None, None, False),
            ("non-boolean done", 200, {"done": 1, "response": text}, None, text, False),
            ("missing response", 200, {"done": True}, None, None, False),
            ("server error", 200, {"done": True, "response": text, "error": "failed"}, None, text, False),
        )
        for label, status, payload, transport, original, completed in cases:
            with self.subTest(label=label):
                body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
                row = {
                    "http_status": status, "raw_body_b64": base64.b64encode(body).decode(),
                    "response": original, "infrastructure_error": None, "transport_error": transport,
                }
                replay_row = scorer.resolve_request(row)
                if completed:
                    self.assertEqual(scorer.replay_text(replay_row), original)
                else:
                    self.assertTrue(scorer.replay_text(replay_row).startswith("Error querying"))
                self.assertEqual(row["response"], original)
                self.assertIsNone(row["infrastructure_error"])
                self.assertEqual(base64.b64decode(row["raw_body_b64"]), body)

    def test_scorer_rejects_a_rewritten_success_response(self):
        body = json.dumps({"done": True, "response": "Expression: x\n"}).encode()
        row = {
            "http_status": 200, "raw_body_b64": base64.b64encode(body).decode(),
            "response": "Expression: x", "infrastructure_error": None,
        }
        with self.assertRaisesRegex(RuntimeError, "differs from the raw HTTP body"):
            scorer.resolve_request(row)

    def test_p3_scorer_rejects_tampered_partial_text_and_failure_flags(self):
        original = next(row for row in self.selected if row["infrastructure_error"])
        for change, error in (
            ({"response": None}, "Stored response"),
            ({"infrastructure_error": None}, "Stored infrastructure status"),
            ({"parsed_expression": "x"}, "archived parsed history"),
        ):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as directory:
                target = Path(directory)
                self.copy_bundle(target)
                with (target / "requests.jsonl").open("w", encoding="utf-8", newline="\n") as journal:
                    for archived in self.archived:
                        row = {key: value for key, value in archived.items() if key != "generation_metadata"}
                        if archived is original:
                            row.update(change)
                        journal.write(json.dumps(row) + "\n")
                self.seal_fixture(target)
                with (
                    patch.object(sys, "argv", ["score_p3_prompt", str(target)]),
                    self.assertRaisesRegex(RuntimeError, rf"{error}.*42-H2, attempt 1, slot 1, P2"),
                ):
                    score_p3_prompt.main()

    def test_seal_rejects_missing_receipt_or_changed_journal_including_discarded_attempt(self):
        for mode in ("missing", "discarded", "truncated", "receipt-count", "different-preflight"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                target = Path(directory)
                self.copy_bundle(target, journal=True)
                journal, seal_path = target / "requests.jsonl", target / p3_collection_seal.SEAL
                if mode == "missing":
                    seal_path.unlink()
                    error = "collection_seal.json is required"
                elif mode in {"discarded", "truncated"}:
                    data = journal.read_bytes()
                    if mode == "discarded":
                        tampered = data.replace(b'"http_status": 503', b'"http_status": 502', 1)
                        self.assertNotEqual(tampered, data)
                    else:
                        tampered = data[:-1]
                    journal.write_bytes(tampered)
                    error = "Request journal SHA-256"
                elif mode == "different-preflight":
                    path = target / "preflight.json"
                    path.write_bytes(path.read_bytes() + b"\n")
                    error = "preflight_sha256 differs"
                else:
                    seal = json.loads(seal_path.read_bytes())
                    seal["actual_requests"] = 180
                    seal_path.write_bytes(preflight.json_bytes(seal))
                    error = "seal counts"
                with (
                    patch.object(sys, "argv", ["score_p3_prompt", str(target)]),
                    patch.object(scorer, "checked_journal") as replay,
                    self.assertRaisesRegex(RuntimeError, error),
                ):
                    score_p3_prompt.main()
                replay.assert_not_called()

    def test_final_seal_records_all_jsonl_rows_and_utc_time_after_journal_fsync(self):
        _, _, bindings = preflight.read_bundle(self.run_dir, online=False)
        with patch.object(p3_collection_seal, "make_receipt", side_effect=AssertionError("Verifier regenerated receipt")):
            data, receipt = p3_collection_seal.verify_collection(self.run_dir, bindings)
        self.assertEqual(receipt["requests_record_count"], len(data.split(b"\n")) - 1)
        self.assertEqual((receipt["planned_requests"], receipt["actual_requests"], receipt["requests_record_count"]),
                         (180, 192, 192))
        self.assertRegex(receipt["completion_timestamp"], r"T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$")
        self.assertEqual(self.collector_trace["seal_events"], [["fsync", 192], ["receipt", 192], ["fsync", 192]])

    def assert_invalid_seal(self, edit, error):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            self.copy_bundle(target, journal=True)
            seal_path = target / p3_collection_seal.SEAL
            seal = json.loads(seal_path.read_bytes())
            edit(seal)
            seal_path.write_bytes(preflight.json_bytes(seal))
            with (
                patch.object(sys, "argv", ["score_p3_prompt", str(target)]),
                patch.object(scorer, "checked_journal") as replay,
                self.assertRaisesRegex(RuntimeError, error),
            ):
                score_p3_prompt.main()
            replay.assert_not_called()

    def test_scoring_rejects_a_seal_without_completion_timestamp(self):
        self.assert_invalid_seal(lambda seal: seal.pop("completion_timestamp"), "missing=.*completion_timestamp")

    def test_scoring_rejects_empty_non_utc_invalid_or_non_string_completion_timestamp(self):
        for value in ("", "2026-11-26", "2026-11-26T03:25:05", "2026-11-26T03:25:05+08:00",
                      "2026-02-30T03:25:05Z", None, 12345):
            with self.subTest(value=value):
                self.assert_invalid_seal(lambda seal: seal.update(completion_timestamp=value), "completion_timestamp")

    def test_scoring_rejects_tampered_requests_record_count(self):
        for count in (191, "192"):
            with self.subTest(count=count):
                self.assert_invalid_seal(lambda seal: seal.update(requests_record_count=count), "requests_record_count")

    def test_scoring_compares_even_self_consistent_counts_against_archived_rows(self):
        self.assert_invalid_seal(
            lambda seal: seal.update(requests_record_count=180, actual_requests=180),
            "requests_record_count differs from the archived JSONL record count",
        )

    def test_interrupted_collection_preserves_partial_journal_without_final_seal(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            self.copy_bundle(target)
            child = subprocess.run(
                [sys.executable, "-B", "-m", "test_p3_validation_preflight", str(target), "--interrupt"],
                cwd=ROOT, env={**os.environ, "PYTHONPATH": str(ROOT / "tests")},
                capture_output=True, encoding="utf-8", check=False,
            )
            self.assertNotEqual(child.returncode, 0)
            self.assertIn("mock interruption", child.stderr)
            self.assertEqual(len((target / "requests.jsonl").read_bytes().splitlines()), 1)
            self.assertFalse((target / p3_collection_seal.SEAL).exists())
            self.assertEqual((target / p3_collection_seal.PENDING_SEAL).read_bytes(), b"")

    def test_guard_is_rechecked_on_a_resealed_discarded_attempt(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            self.copy_bundle(target, journal=True)
            journal = target / "requests.jsonl"
            rows = journal.read_bytes().splitlines()
            index = next(i for i, line in enumerate(rows) if json.loads(line)["guard_pass"])
            self.assertLess(index, 12)
            changed = json.loads(rows[index])
            changed["guard_pass"] = False
            rows[index] = json.dumps(changed).encode("utf-8")
            journal.write_bytes(b"\n".join(rows) + b"\n")
            self.seal_fixture(target)
            identity = f"{changed['block']}, attempt 1, slot {changed['slot']}, {changed['variant']}"
            with (
                patch.object(sys, "argv", ["score_p3_prompt", str(target)]),
                self.assertRaisesRegex(RuntimeError, rf"Archived Guard Pass.*{identity}"),
            ):
                score_p3_prompt.main()

    def test_scorer_rejects_parsed_history_mismatch_with_slot_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            for name in ("online_manifest.json", "selection_manifest.json"):
                (target / name).write_bytes((self.run_dir / name).read_bytes())
            journal = (self.run_dir / "requests.jsonl").read_text(encoding="utf-8").splitlines()
            row = json.loads(journal[-1])
            self.assertFalse(row["infrastructure_error"])
            row["parsed_expression"] = "tampered history"
            journal[-1] = json.dumps(row)
            (target / "requests.jsonl").write_text("\n".join(journal) + "\n", encoding="utf-8")
            with (
                self.assertRaisesRegex(RuntimeError, r"Parsed expression.*314159-H5, attempt 1, slot 4, P3"),
            ):
                scorer.checked_journal(
                    target, self.manifest, self.freeze, variants=VARIANTS, selection_file="selection_manifest.json",
                )

    def test_depth_and_duplicate_diagnostics_use_official_ast_and_mechanical_keys(self):
        expressions = ["x OR (x AND y)", "x OR (x AND (x OR y))", "y AND x", "x AND y", None]
        from boolean_dataset import BooleanExpression
        seen = set()
        diagnostics = [scorer.structural_diagnostics(self.benchmark, BooleanExpression, expr, seen)
                       for expr in expressions]
        result = [(d["ast_depth"], d["ast_depth_violation"], d["duplicate_mechanistic_key"]) for d in diagnostics]
        self.assertEqual(result, [(2, False, False), (3, True, False), (1, False, False),
                                  (1, False, True), (None, False, False)])
        self.assertEqual(scorer.diagnostic_rates(diagnostics),
                         {"ast_depth_violation_rate": 0.2, "duplicate_rate": 0.2})

    def test_cost_summary_keeps_nulls_and_includes_timed_failures(self):
        first = {"generation_metadata": {"prompt_eval_count": 100, "eval_count": 5}, "elapsed_seconds": 0.2}
        failed = {"generation_metadata": {}, "elapsed_seconds": 0.6, "infrastructure_error": "timeout"}
        missing = {"generation_metadata": {"prompt_eval_count": 25}, "elapsed_seconds": None}
        cost = scorer.request_cost([first, failed, missing])
        self.assertEqual((cost["input_tokens"], cost["output_tokens"]), (125, 5))
        self.assertEqual((cost["token_valid_records"], cost["token_missing_records"]), (1, 2))
        self.assertAlmostEqual(cost["mean_request_seconds"], 0.4)
        self.assertEqual(cost["timing_valid_records"], 2)
        self.assertIsNone(scorer.request_cost([failed])["input_tokens"])
        self.assertIsNone(scorer.request_cost([missing])["output_tokens"])
        self.assertIsNone(scorer.request_cost([missing])["mean_request_seconds"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--interrupt", action="store_true")
    args = parser.parse_args()
    mock_collector_process(args.run_dir, interrupted=args.interrupt)
