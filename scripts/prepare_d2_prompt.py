"""Freeze the D2 split and verify the collection path without generating."""

import argparse
import json
import os
import random
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path
from urllib.request import Request, urlopen

from scripts.run_d2_prompt import D2_VARIANTS as VARIANTS
from scripts.run_d2_prompt import MODEL_DIGEST, OPTIONS, SEEDS, render, sha256

ROOT = Path(__file__).resolve().parents[1]
UPSTREAM = ROOT.parent / "HypoSpace"
DATASET = ROOT / "data/d1/boolean_basic_depth2.json"
SPLIT_SEED = 33550336


def document(path, value):
    path.write_bytes((json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))


def command(*args):
    return subprocess.check_output(args, cwd=ROOT, text=True, encoding="utf-8", stderr=subprocess.STDOUT)


def metadata_request(path, value=None):
    body = json.dumps(value).encode() if value else None
    with urlopen(Request("http://127.0.0.1:11434/api/" + path, body, {"Content-Type": "application/json"}), timeout=15) as response:
        return json.load(response)


def fingerprint(observations):
    rows = sorted((o["inputs"]["x"], o["inputs"]["y"], o["output"]) for o in observations)
    return sha256(json.dumps(rows).encode())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path)
    run_dir = parser.parse_args().run_dir.resolve()
    if os.environ.get("PYTHONHASHSEED") != "0":
        raise SystemExit("Set PYTHONHASHSEED=0 for the official template")
    if sys.version_info[:2] != (3, 11):
        raise SystemExit("Use the project Python 3.11 environment")
    config = json.loads((ROOT / "configs/boolean_smoke.json").read_bytes())
    commit = command("git", "-C", str(UPSTREAM), "rev-parse", "HEAD").strip()
    if commit != config["upstream_commit"] or command("git", "-C", str(UPSTREAM), "status", "--porcelain").strip():
        raise SystemExit("The pinned upstream must be present and clean")
    sys.path.insert(0, str(UPSTREAM / "boolean"))
    from boolean_benchmark import BooleanBenchmarkRefined
    from boolean_dataset import BooleanObservation

    benchmark = BooleanBenchmarkRefined(str(DATASET))
    if benchmark.variables != ["x", "y"] or benchmark.operators != {"AND", "OR"} or benchmark.max_depth != 2:
        raise RuntimeError("Unexpected Boolean task")
    prompt_dir = ROOT / "prompts/d2"
    templates = {p: (prompt_dir / f"{p}.txt").read_text(encoding="utf-8") for p in VARIANTS}
    hashes = {p: sha256(text.encode()) for p, text in templates.items()}
    hash_manifest = (prompt_dir / "README.md").read_text(encoding="utf-8")
    for p, digest in hashes.items():
        if f"| {p} | `{digest}` |" not in hash_manifest:
            raise RuntimeError(f"Unapproved prompt digest for {p}")
    for p in ("P1", "P2"):
        examples = re.findall(r"^ +Expression: (.+)$", templates[p], re.MULTILINE)
        if len(examples) != (4 if p == "P1" else 6) or any(
            re.search(r"\b(?:NOT|NOR|z)\b", example) or not benchmark._in_space(example) for example in examples
        ) or re.search(r"\b(?:NOR|z)\b|\bNOT\b(?! use formats like:)", templates[p]):
            raise RuntimeError(f"Illegal text or example in {p}")

    d1_outputs = sorted((ROOT / "results/d1").glob("*/result.json"))
    excluded = {entry["observation_set_id"] for path in d1_outputs
                for entry in json.loads(path.read_bytes())["per_sample_results"]}
    if not excluded:
        raise RuntimeError("D1 split could not be verified")
    available = {g["observation_set_id"]: g["observations"] for g in benchmark.all_observation_sets}
    rng = random.Random(SPLIT_SEED)
    quotas = {"development": (1, 2, 1, 1), "pilot": (1, 2, 1, 1), "formal": (3, 5, 5, 2)}
    splits = {name: [] for name in quotas}
    excluded_fingerprints = {fingerprint(available[group_id]) for group_id in excluded}
    for n in range(1, 5):
        group_ids = sorted(gid for gid, obs in available.items() if len(obs) == n and fingerprint(obs) not in excluded_fingerprints)
        rng.shuffle(group_ids)
        position = 0
        for name, quotas_by_size in quotas.items():
            end = position + quotas_by_size[n - 1]
            if len(group_ids) < end:
                raise RuntimeError(f"Insufficient groups with {n} observations")
            splits[name].extend(group_ids[position:end])
            position = end
    fingerprints = [fingerprint(available[gid]) for split in splits.values() for gid in split]
    if len(set(fingerprints)) != 25 or set(fingerprints) & excluded_fingerprints:
        raise RuntimeError("Observation content overlaps across splits")

    observations_by_id = {}
    for gid in splits["development"]:
        rows = [BooleanObservation({v: row["inputs"][v] for v in ("x", "y")}, row["output"]) for row in available[gid]]
        observations_by_id[gid] = "\n".join(row.to_string() for row in rows)
        for history in ([], ["x", "NOT y", "x AND y"]):
            if render(templates["P0"], observations_by_id[gid], history) != benchmark.create_prompt(rows, history):
                raise RuntimeError(f"P0 differs from the pinned official renderer for {gid}")

    model = next((m for m in metadata_request("tags")["models"] if m["name"] == "qwen2.5:3b"), None)
    if not model or model["digest"] != MODEL_DIGEST:
        raise RuntimeError("D2 model digest differs from D1")
    version = metadata_request("version")["version"]
    model_show = metadata_request("show", {"model": model["name"]})
    gpu_header = "timestamp,name,memory.total,memory.used,utilization.gpu"
    gpu = command("nvidia-smi", "--query-gpu=" + gpu_header, "--format=csv,noheader,nounits").strip()
    blocks = []
    for seed_index, seed in enumerate(SEEDS):
        for group_index, gid in enumerate(splits["development"]):
            rotation = (seed_index + group_index) % len(VARIANTS)
            blocks.append({
                "id": f"s{seed_index + 1}-g{group_index + 1}", "seed": seed, "group_id": gid,
                "observations": observations_by_id[gid], "prompt_order": VARIANTS[rotation:] + VARIANTS[:rotation],
                "slot_seeds": [seed + 4 * group_index + slot for slot in range(4)],
            })
    plan = {
        "model": model["name"], "model_digest": model["digest"], "options": OPTIONS, "templates": templates,
        "blocks": blocks, "block_rerun_min_failures": 2,
    }
    if sum(len(b["slot_seeds"]) * len(b["prompt_order"]) for b in blocks) != 180:
        raise RuntimeError("Unexpected planned request count")

    run_dir.mkdir(parents=True, exist_ok=False)
    document(run_dir / "split_manifest.json", {
        "split_seed": SPLIT_SEED, "excluded_d1": sorted(excluded), "groups": splits,
        "observation_fingerprints": {gid: fingerprint(available[gid]) for ids in splits.values() for gid in ids},
        "development_observation_counts": dict(Counter(len(available[gid]) for gid in splits["development"])),
    })
    document(run_dir / "online_manifest.json", plan)
    document(run_dir / "freeze.json", {
        "upstream_commit": commit, "dataset_sha256": sha256(DATASET.read_bytes()),
        "split_manifest_sha256": sha256((run_dir / "split_manifest.json").read_bytes()),
        "online_manifest_sha256": sha256((run_dir / "online_manifest.json").read_bytes()),
        "collector_sha256": sha256((ROOT / "scripts/run_d2_prompt.py").read_bytes()),
        "prompt_hashes": hashes, "rollout_seeds": SEEDS, "planned_requests": 180,
        "model": model, "ollama_version": version, "model_parameters": model_show.get("parameters"),
        "model_template_sha256": sha256(model_show.get("template", "").encode()),
        "python": sys.version, "gpu_before": gpu,
        "retry_rule": "Two or more infrastructure failures in a group x seed block trigger one entire 12-slot rerun. Use rerun for analysis; retain both attempts.",
    })
    tests = command(sys.executable, "-B", "-m", "unittest", "discover", "-s", "tests", "-p", "test_d2*.py", "-v")
    probe = command(sys.executable, "-B", "-m", "scripts.run_d2_prompt", str(run_dir), "--probe-only").strip()
    if "existing answer file denied" not in probe:
        raise RuntimeError("Collector could not verify the answer-file read barrier")
    document(run_dir / "preflight.json", {
        "online_manifest_sha256": sha256((run_dir / "online_manifest.json").read_bytes()),
        "checks": {
            "01_p0_official_exact": "all five development groups, empty and populated history",
            "02_p1_p2_legal_examples": "official in-space check; no NOT/NOR/z examples",
            "03_full_prompts_and_hashes": hashes,
            "04_excluded_d1": sorted(excluded),
            "05_disjoint_ids_and_fingerprints": {k: len(v) for k, v in splits.items()},
            "06_online_answers_denied": probe,
            "07_archive_and_guard_independence": "D1 and known-safe raw replay plus collector tests",
            "08_single_http_per_slot": "one post per slot, non-redirecting HTTPConnection; mocked block verifies calls",
            "09_request_telemetry": "collector tests verify paired seeds, parameters and original journal fields",
            "10_configuration_frozen": "freeze.json, split_manifest.json, online_manifest.json",
        },
        "tests": tests,
    })
    print(tests)
    print(probe)
    print(f"Frozen 180 planned slots at {run_dir}")


if __name__ == "__main__":
    main()
