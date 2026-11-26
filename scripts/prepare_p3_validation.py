"""Prepare the registered P3 validation manifests offline without generating."""

import argparse
from collections import Counter
from pathlib import Path
from tempfile import TemporaryDirectory

from scripts import p3_preflight as preflight
from scripts import run_d2_prompt as collector
from scripts.prepare_d2_prompt import fingerprint as observation_fingerprint


def checked_selection():
    split_data = preflight.D2_SPLIT.read_bytes()
    dataset_data = preflight.DATASET.read_bytes()
    if collector.sha256(split_data) != preflight.D2_SPLIT_SHA256:
        raise RuntimeError("Historical D2 split has changed")
    if collector.sha256(dataset_data) != preflight.DATASET_SHA256:
        raise RuntimeError("Registered Boolean dataset has changed")
    split = preflight.load_object(split_data, "D2 split")
    dataset = preflight.load_object(dataset_data, "Boolean dataset")
    observations = {
        group["observation_set_id"]: group["observations"]
        for groups in dataset["datasets_by_n_observations"].values() for group in groups
    }
    fingerprints = {group_id: observation_fingerprint(rows) for group_id, rows in observations.items()}
    if len(observations) != 35 or len(set(fingerprints.values())) != 35 or any(
        digest != preflight.fingerprint(group_id) for group_id, digest in fingerprints.items()
    ):
        raise RuntimeError("Public group IDs and observation fingerprints are inconsistent")
    excluded = split["excluded_d1"] + [
        group_id for groups in split["groups"].values() for group_id in groups
    ]
    if len(excluded) != 26 or len(set(excluded)) != 26 or any(
        fingerprints[group_id] != digest for group_id, digest in split["observation_fingerprints"].items()
    ):
        raise RuntimeError("Historical D1/D2/Pilot/formal groups do not match the dataset")
    remaining = set(observations) - set(excluded)
    selected = []
    for count, quota in ((1, 1), (2, 2), (3, 2)):
        ranked = sorted(
            (group_id for group_id in remaining if len(observations[group_id]) == count),
            key=lambda group_id: (collector.sha256(f"p3-heldout-v1:{group_id}".encode()), group_id),
        )
        selected.extend(ranked[:quota])
    if tuple(selected) != collector.P3_GROUPS or remaining - set(selected) != set(preflight.RESERVED_GROUPS):
        raise RuntimeError("Held-out group selection differs from the registered H1-H5 plan")
    return preflight.registered_selection()


def online_manifest():
    templates = {}
    for condition, filename in preflight.PROMPT_FILES.items():
        raw = (preflight.ROOT / "prompts/d2" / filename).read_bytes()
        if collector.sha256(raw) != collector.P3_PROMPT_HASHES[condition]:
            raise RuntimeError(f"Prompt file differs from the registered hash: {filename}")
        templates[condition] = raw.decode("utf-8")

    blocks = []
    for seed_index, seed in enumerate(collector.SEEDS):
        for group_index, group_id in enumerate(collector.P3_GROUPS):
            rotation = (seed_index + group_index) % len(collector.P3_VARIANTS)
            conditions = collector.P3_VARIANTS
            first_seed = seed + 4 * group_index
            blocks.append({
                "id": f"{seed}-H{group_index + 1}", "group_id": group_id,
                "observations": collector.public_observation_text(group_id), "seed": seed,
                "slot_seeds": list(range(first_seed, first_seed + 4)),
                "prompt_order": list(conditions[rotation:] + conditions[:rotation]),
            })
    budgets = Counter(condition for block in blocks for _ in block["slot_seeds"]
                      for condition in block["prompt_order"])
    if dict(budgets) != dict.fromkeys(collector.P3_VARIANTS, 60):
        raise RuntimeError("Each Prompt must have 60 planned slots")
    return {
        "model": preflight.MODEL_NAME, "model_digest": collector.MODEL_DIGEST, "options": collector.OPTIONS,
        "templates": templates, "blocks": blocks, "block_rerun_min_failures": 2,
    }


def prepare(run_dir):
    run_dir = run_dir.resolve()
    if run_dir.exists():
        raise FileExistsError(f"Refusing to overwrite an existing run directory: {run_dir}")
    selection = preflight.json_bytes(checked_selection())
    manifest = preflight.json_bytes(online_manifest())
    preflight.verify_upstream()
    run_dir.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=run_dir.parent, prefix=".p3-prepare-") as directory:
        staging = Path(directory)
        environment = preflight.probe_environment(staging)
        freeze = {
            "schema_version": preflight.SCHEMA_VERSION,
            "online_manifest_sha256": collector.sha256(manifest),
            "selection_manifest_sha256": collector.sha256(selection),
            "dataset_sha256": preflight.DATASET_SHA256, **preflight.source_hashes(),
            "scoring_versions": preflight.scoring_versions(),
            "upstream_commit": preflight.UPSTREAM_COMMIT, "ollama_version": preflight.OLLAMA_VERSION,
            "model": preflight.registered_model(), "options": collector.OPTIONS,
            "prompt_hashes": collector.P3_PROMPT_HASHES, "rollout_seeds": list(collector.SEEDS),
            "planned_requests": preflight.PLANNED_REQUESTS,
        }
        freeze_bytes = preflight.json_bytes(freeze)
        receipt = {
            **{field: freeze[field] for field in preflight.COMMON_FIELDS},
            "freeze_sha256": collector.sha256(freeze_bytes), "model_digest": freeze["model"]["digest"],
            "environment": environment, "checks": dict.fromkeys(preflight.REQUIRED_CHECKS, True),
        }
        files = {
            preflight.SELECTION_FILE: selection, "online_manifest.json": manifest,
            "freeze.json": freeze_bytes, "preflight.json": preflight.json_bytes(receipt),
        }
        for name, data in files.items():
            (staging / name).write_bytes(data)
        preflight.read_bundle(staging, online=False)
        run_dir.mkdir(exist_ok=False)
        for name in files:
            (staging / name).rename(run_dir / name)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path)
    prepare(parser.parse_args().run_dir)


if __name__ == "__main__":
    main()
