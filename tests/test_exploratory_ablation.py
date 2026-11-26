import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from scripts import run_exploratory as primary
from scripts import run_exploratory_ablation as ablation
from scripts.run_exploratory_ablation import collect, load_bundle, matched_slots, prepare, replay


class AblationTests(TestCase):
    def test_ablation_uses_exactly_the_frozen_fscb_slots(self):
        manifest = primary.registered_manifest()
        slots = list(matched_slots(manifest))
        expected = [slot for slot in primary.expected_slots(manifest) if slot["method"] == "FSCB"]
        self.assertEqual(len(slots), 48)
        for index, (observed, original) in enumerate(zip(slots, expected, strict=True), 1):
            self.assertEqual(observed, {
                **original, "method": "FSCB-no-bonus", "index": index, "primary_index": original["index"],
            })

    def test_synthetic_exposure_rewards_and_official_replay(self):
        manifest = primary.registered_manifest()
        manifest["blocks"] = [{
            "group_id": "synthetic", "observations": "(x=0, y=0) -> 0", "seed": 42,
            "slot_seeds": [42, 43, 44, 45], "first_method": "ACB",
        }]
        responses = iter(("x", "y", "x AND x", "NOT y"))
        log = io.StringIO()

        def post(_):
            return 200, json.dumps({"done": True, "response": next(responses)}).encode(), None

        collect(manifest, log, post)
        rows = [json.loads(line) for line in log.getvalue().splitlines()]
        self.assertEqual([row["feedback"]["reward"] for row in rows], [1., 1., .2, -.5])
        self.assertTrue(any(arm["mode"] == "frontier" for arm in rows[1]["decision"]["available"]))
        labels = {"synthetic": {
            "observation_set_id": "synthetic", "n_observations": 1,
            "observations": [{"inputs": {"x": 0, "y": 0}, "output": 0}],
            "ground_truth_expressions": [
                {"truth_table": {}, "mechanistic_key": ("VAR", "x")},
                {"truth_table": {}, "mechanistic_key": ("VAR", "y")},
            ],
        }}
        [scored] = replay(manifest, rows, labels)
        self.assertEqual(scored["official"]["recovery_rate"], 1.)
        self.assertEqual(scored["official"]["valid_rate"], .75)
        self.assertEqual(scored["official"]["novelty_rate"], .5)
        rows[1]["feedback"]["reward"] = .8419
        with self.assertRaisesRegex(RuntimeError, "trace differs"):
            replay(manifest, rows, labels)

    def test_separate_freeze_does_not_require_or_write_primary_results(self):
        with TemporaryDirectory() as temp:
            parent = Path(temp) / "primary"
            run = Path(temp) / "ablation"
            primary.prepare(parent)
            baseline = {path.name: path.read_bytes() for path in parent.iterdir()}
            prepare(parent, run)
            manifest, _ = load_bundle(parent, run)
            self.assertEqual(len(list(matched_slots(manifest))), 48)
            self.assertEqual({path.name: path.read_bytes() for path in parent.iterdir()}, baseline)
            self.assertEqual({path.name for path in run.iterdir()}, {"freeze.json", "online_manifest.json"})
            freeze_path = run / "freeze.json"
            invalid = json.loads(freeze_path.read_bytes())
            invalid["beta"] = .25
            freeze_path.write_text(json.dumps(invalid), encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "ablation freeze"):
                load_bundle(parent, run)

    def test_full_mock_collection_seals_and_replays_both_conditions(self):
        manifest = primary.registered_manifest()
        labels = {}
        # Replace the public rows and IDs with synthetic fixtures while keeping the slot schedule.
        aliases = {group: f"synthetic-{i}" for i, group in enumerate(primary.pinned.RESERVED_GROUPS, 1)}
        for block in manifest["blocks"]:
            group_id = aliases[block["group_id"]]
            block.update(group_id=group_id, observations="(x=0, y=0) -> 0")
            labels[group_id] = {
                "observation_set_id": group_id, "n_observations": 1,
                "observations": [{"inputs": {"x": 0, "y": 0}, "output": 0}],
                "ground_truth_expressions": [
                    {"truth_table": {}, "mechanistic_key": repr(("VAR", "x"))},
                    {"truth_table": {}, "mechanistic_key": repr(("VAR", "y"))},
                ],
            }
        with TemporaryDirectory() as temp:
            root = Path(temp)
            parent = root / "primary"
            run = root / "ablation"
            parent.mkdir()
            run.mkdir()
            data = root / "synthetic.json"
            data.write_bytes(primary.pinned.json_bytes({
                "datasets_by_n_observations": {"1": list(labels.values())},
            }))
            main_stream, ablation_stream = io.StringIO(), io.StringIO()
            primary.collect(manifest, main_stream, lambda _: (200, b'{"done":true,"response":"x"}', None))
            collect(manifest, ablation_stream, lambda _: (200, b'{"done":true,"response":"y"}', None))
            main_bytes = main_stream.getvalue().encode()
            ablation_bytes = ablation_stream.getvalue().encode()
            main_freeze, ablation_freeze = "a" * 64, "b" * 64
            (parent / "requests.jsonl").write_bytes(main_bytes)
            (run / "requests.jsonl").write_bytes(ablation_bytes)
            (parent / "collection_seal.json").write_bytes(primary.pinned.json_bytes(
                primary.collection_seal(main_bytes, main_freeze),
            ))
            (run / "collection_seal.json").write_bytes(ablation.seal(ablation_bytes, ablation_freeze))
            main = primary.score_records(manifest, [json.loads(line) for line in main_bytes.splitlines()], labels)
            (parent / "official-results.json").write_bytes(primary.pinned.json_bytes(main))
            with (
                patch.object(primary, "load_bundle", return_value=(manifest, main_freeze)),
                patch.object(ablation, "load_bundle", return_value=(manifest, ablation_freeze)),
                patch.multiple(primary.pinned, DATASET=data, DATASET_SHA256=primary.sha256(data.read_bytes())),
            ):
                recovery = ablation.score(parent, run)["recovery_rate"]
            self.assertEqual(recovery["full"], .5)
            self.assertEqual(recovery["no_bonus"], .5)
            self.assertEqual(recovery["ties"], 12)
