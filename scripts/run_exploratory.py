"""Prepare, collect, or replay the 96-slot exploratory Boolean comparison."""

import argparse
import base64
import io
import json
import os
import re
import sys
from collections import Counter, defaultdict
from dataclasses import asdict
from pathlib import Path
from random import Random
from statistics import mean, stdev
from time import perf_counter

from scripts import p3_preflight as pinned
from scripts.candidate_history import BooleanBenchmarkRefined, BooleanObservation
from scripts.fscb_hypo import (
    ALPHA, BETA, CAPACITIES, FRONTIER_BYTES, HISTORY_BYTES, KAPPA, METHODS, RIDGE,
    TEMPERATURES, WARMUP_FEEDBACK, Controller, frontier_hint,
)
from scripts.prepare_p3_validation import checked_selection
from scripts.run_d2_prompt import (
    MODEL_DIGEST, OPTIONS, P3_GROUPS, P3_PROMPT_HASHES, SEEDS, file_lockdown,
    interpret_generation_response, post_once, public_observation_text, sha256,
)
from scripts.score_d2_prompt import LEXICAL_SAFETY, request_cost

P3_ARCHIVE = pinned.ROOT / "results/p3/controlled-validation-20261126-210216-final"
QUERIES = 4
PLANNED = 96
METRICS = ("valid_rate", "novelty_rate", "recovery_rate")
SOURCE_PATHS = (
    "scripts/run_exploratory.py", "scripts/fscb_hypo.py", "scripts/candidate_history.py",
    "scripts/run_d2_prompt.py", "scripts/run_boolean_smoke.py", "scripts/p3_preflight.py",
    "scripts/ollama_process.py", "scripts/prepare_p3_validation.py",
    "scripts/prepare_d2_prompt.py", "scripts/score_d2_prompt.py", "uv.lock",
)
OUTPUTS = ("requests.jsonl", "collection_seal.json", ".collection_seal.incomplete", "official-results.json")


def verify_selection():
    """Offline only: verify the registered IDs against historical exposure."""
    checked_selection()
    split = json.loads(pinned.D2_SPLIT.read_bytes())
    d1 = {row["observation_set_id"]
          for path in (pinned.ROOT / "results/d1").glob("*/result.json")
          for row in json.loads(path.read_bytes())["per_sample_results"]}
    d2 = {json.loads(line)["group_id"] for line in (pinned.D2_SPLIT.parent / "requests.jsonl").read_bytes().splitlines()}
    p3_journal = (P3_ARCHIVE / "requests.jsonl").read_bytes()
    p3_seal = json.loads((P3_ARCHIVE / "collection_seal.json").read_bytes())
    if sha256(p3_journal) != p3_seal["requests_sha256"]:
        raise RuntimeError("Cannot verify the historical P3 exposure journal")
    p3 = {json.loads(line)["group_id"] for line in p3_journal.splitlines()}
    if (d1, d2, p3) != (set(split["excluded_d1"]), set(split["groups"]["development"]), set(P3_GROUPS)):
        raise RuntimeError("Historical exposure disagrees with the archived allocation")
    forbidden = d1 | d2 | p3 | set(split["groups"]["pilot"]) | set(split["groups"]["formal"])
    reserved = set(pinned.RESERVED_GROUPS)
    if (len(reserved) != 4 or reserved & forbidden
            or {pinned.fingerprint(group) for group in reserved}
            & {pinned.fingerprint(group) for group in forbidden}):
        raise RuntimeError("Exploratory groups overlap historical or reserved observations")
    return {group: pinned.fingerprint(group) for group in sorted(reserved)}


def registered_manifest():
    prompt = (pinned.ROOT / "prompts/d2/P1.txt").read_bytes()
    if sha256(prompt) != P3_PROMPT_HASHES["P1"]:
        raise RuntimeError("Frozen base Prompt changed")
    blocks = []
    for seed_index, seed in enumerate(SEEDS):
        groups = sorted(pinned.RESERVED_GROUPS)
        Random(seed).shuffle(groups)
        for group_index, group_id in enumerate(groups):
            start = seed + QUERIES * group_index
            blocks.append({"group_id": group_id, "observations": public_observation_text(group_id),
                           "seed": seed, "slot_seeds": list(range(start, start + QUERIES)),
                           "first_method": METHODS[(seed_index + group_index) % 2]})
    return {"schema_version": 1, "model": pinned.MODEL_NAME, "model_digest": MODEL_DIGEST,
            "options": {key: OPTIONS[key] for key in ("num_ctx", "num_predict")},
            "template": prompt.decode("utf-8"), "blocks": blocks}


def expected_slots(manifest):
    index = 0
    for block in manifest["blocks"]:
        first = METHODS.index(block["first_method"])
        for offset, seed in enumerate(block["slot_seeds"]):
            for method in (METHODS[(first + offset) % 2], METHODS[(first + offset + 1) % 2]):
                index += 1
                yield {"index": index, "group_id": block["group_id"], "seed": block["seed"],
                       "slot": offset + 1, "model_seed": seed, "method": method}


def public_rows(text):
    return [BooleanObservation({"x": int(x), "y": int(y)}, int(output))
            for x, y, output in re.findall(r"\(x=([01]), y=([01])\) -> ([01])", text)]


def render_prompt(template, observations, decision):
    prompt = template.replace("{{OBSERVATIONS}}", observations).replace("{{PRIOR_EXPRESSIONS}}", decision.history)
    if decision.target:
        boundary = "            Output format:"
        before, mark, after = prompt.partition(boundary)
        if not mark:
            raise RuntimeError("Frozen template lost the Frontier insertion point")
        prompt = before + "            " + frontier_hint(decision.target) + "\n\n" + mark + after
    return prompt


def collect(manifest, journal, post):
    """One caller-provided transport call per slot. No answer-bearing input."""
    blocks = {(block["seed"], block["group_id"]): block for block in manifest["blocks"]}
    controllers = {}
    for slot in expected_slots(manifest):
        seed, method = slot["seed"], slot["method"]
        block = blocks[seed, slot["group_id"]]
        if (seed, method) not in controllers:
            controllers[seed, method] = Controller(method, seed)
        controller = controllers[seed, method]
        if slot["slot"] == 1:
            controller.start_group(public_rows(block["observations"]), seed=block["slot_seeds"][0])
        decision = controller.decide(slot["slot"] - 1, len(block["slot_seeds"]))
        prompt = render_prompt(manifest["template"], block["observations"], decision)
        payload = {"model": manifest["model"], "prompt": prompt, "stream": False,
                   "options": {**manifest["options"], "seed": slot["model_seed"],
                               "temperature": decision.arm.temperature}}
        start = perf_counter()
        status, body, transport_error = post(payload)
        elapsed = perf_counter() - start
        raw, failure = interpret_generation_response(status, body, transport_error)
        text = raw.get("response")
        feedback = controller.observe(decision, text if failure is None else None)
        row = {**slot, "decision": asdict(decision), "feedback": feedback, "request": payload,
               "http_status": status, "transport_error": transport_error, "infrastructure_error": failure,
               "raw_body_b64": base64.b64encode(body).decode("ascii"),
               "response": text if isinstance(text, str) else None,
               "elapsed_seconds": elapsed, "prompt_eval_count": raw.get("prompt_eval_count"),
               "eval_count": raw.get("eval_count")}
        journal.write(json.dumps(row, ensure_ascii=False) + "\n")
        journal.flush()


def expected_freeze(manifest_hash):
    return {"schema_version": 1, "upstream_commit": pinned.UPSTREAM_COMMIT,
            "dataset_sha256": pinned.DATASET_SHA256, "ollama_version": pinned.OLLAMA_VERSION,
            "model": pinned.registered_model(), "base_prompt_sha256": P3_PROMPT_HASHES["P1"],
            "online_manifest_sha256": manifest_hash, "planned_requests": PLANNED,
            "source_hashes": {path: pinned.digest_file(pinned.ROOT / path) for path in SOURCE_PATHS},
            "python_version": ".".join(map(str, sys.version_info[:3])), "packages": pinned.scoring_versions(),
            "policy": {"temperatures": TEMPERATURES, "ridge": RIDGE, "alpha": ALPHA,
                       "warmup_feedback": WARMUP_FEEDBACK, "beta": BETA, "kappa": KAPPA,
                       "bucket_capacity": CAPACITIES, "history_bytes": HISTORY_BYTES,
                       "frontier_bytes": FRONTIER_BYTES, "retry": "none"}}


def prepare(run_dir):
    if run_dir.exists():
        raise FileExistsError(f"Use a new experiment directory: {run_dir}")
    pinned.verify_upstream()
    groups = verify_selection()
    manifest = pinned.json_bytes(registered_manifest())
    freeze = pinned.json_bytes(expected_freeze(sha256(manifest)))
    run_dir.mkdir(parents=True, exist_ok=False)
    (run_dir / "online_manifest.json").write_bytes(manifest)
    (run_dir / "freeze.json").write_bytes(freeze)
    return {"groups": groups, "planned_requests": PLANNED, "freeze_sha256": sha256(freeze),
            "online_manifest_sha256": sha256(manifest)}


def load_bundle(run_dir):
    pinned.verify_upstream()
    manifest_bytes = (run_dir / "online_manifest.json").read_bytes()
    if manifest_bytes != pinned.json_bytes(registered_manifest()):
        raise RuntimeError("Public manifest differs from the registered allowlisted plan")
    freeze_bytes = (run_dir / "freeze.json").read_bytes()
    if freeze_bytes != pinned.json_bytes(expected_freeze(sha256(manifest_bytes))):
        raise RuntimeError("Freeze differs from the registered code, environment or experiment")
    manifest = json.loads(manifest_bytes)
    if len(list(expected_slots(manifest))) != PLANNED:
        raise RuntimeError("Unexpected request budget")
    return manifest, sha256(freeze_bytes)


def collect_live(run_dir):
    manifest, freeze_hash = load_bundle(run_dir)
    if any((run_dir / name).exists() for name in OUTPUTS):
        raise FileExistsError("This run directory already has experiment outputs")
    environment = pinned.probe_environment(run_dir)
    (run_dir / "launch-environment.json").write_bytes(pinned.json_bytes(environment))
    pending = run_dir / ".collection_seal.incomplete"
    with (run_dir / "requests.jsonl").open("x+", encoding="utf-8", newline="\n") as journal, pending.open("xb") as seal:
        sys.addaudithook(file_lockdown)
        try:
            pinned.DATASET.open("rb").close()
        except PermissionError:
            pass
        else:
            raise RuntimeError("The online process can still read the answer-bearing dataset")
        collect(manifest, journal, post_once)
        journal.flush()
        os.fsync(journal.fileno())
        journal.seek(0)
        content = journal.buffer.read()
        seal.write(pinned.json_bytes(collection_seal(content, freeze_hash)))
        seal.flush()
        os.fsync(seal.fileno())
    os.link(pending, run_dir / "collection_seal.json")
    pending.unlink()


def collection_seal(content, freeze_hash):
    if not content.endswith(b"\n") or len(content.splitlines()) != PLANNED:
        raise RuntimeError("Only a complete 96-slot journal can be scored")
    return {"requests_sha256": sha256(content), "bytes": len(content), "records": PLANNED,
            "freeze_sha256": freeze_hash}


def verified_online_trace(manifest, records):
    """Reproduce the decision trace using archived HTTP bytes as the transport."""
    stored = iter(records)

    def replay(payload):
        row = next(stored)
        if payload != row["request"]:
            raise RuntimeError(f"Prompt or sampling parameters differ at slot {row['index']}")
        return row["http_status"], base64.b64decode(row["raw_body_b64"], validate=True), row["transport_error"]

    reproduction = io.StringIO()
    collect(manifest, reproduction, replay)
    for line, original in zip(reproduction.getvalue().splitlines(), records, strict=True):
        regenerated = json.loads(line)
        if any(original.get(field) != value for field, value in regenerated.items() if field != "elapsed_seconds"):
            raise RuntimeError(f"Online feedback or archived response differs at slot {original['index']}")


def action_summary(rows):
    opportunities = Counter()
    chosen = Counter()
    updates = Counter()
    hits = Counter()
    novelty = 0
    frontier_attempts = 0
    for row in rows:
        decision, feedback = row["decision"], row["feedback"]
        arm = decision["arm"]
        label = f"{arm['mode']}@{arm['temperature']}"
        opportunities.update(f"{item['mode']}@{item['temperature']}" for item in decision["available"])
        chosen[label] += 1
        updates[label] += feedback["reward"] is not None
        novelty += feedback["status"] == "VALID_NEW"
        frontier_attempts += feedback["frontier_hit"] is not None
        if feedback["frontier_hit"]:
            hits[decision["target"]] += 1
    return {"arm_opportunities": opportunities, "arm_requests": chosen, "arm_updates": updates,
            "valid_new_events": novelty, "frontier_scored_attempts": frontier_attempts, "frontier_hits": hits}


def score_records(manifest, records, labels, *, reviewed=()):
    """Offline only: check journal and run the original HypoSpace evaluator."""
    from modules.llm_interface import LLMInterface

    class Replay(LLMInterface):
        def __init__(self, responses):
            self.responses = iter(responses)
            self.used = 0

        def query(self, prompt):
            self.used += 1
            return next(self.responses)

        def get_name(self):
            return "Sealed exploratory replay"

    verified_online_trace(manifest, records)
    benchmark = BooleanBenchmarkRefined.__new__(BooleanBenchmarkRefined)
    benchmark.variables, benchmark.operators, benchmark.max_depth = ["x", "y"], {"AND", "OR"}, 2
    benchmark.mechanistic_opts = dict.fromkeys(
        ("apply_commutativity", "apply_idempotence_and_or", "flatten_associativity"), True,
    )
    benchmark.complete_dataset = {"metadata": {"mechanistic_opts": benchmark.mechanistic_opts}}
    trajectories = defaultdict(list)
    verified_records = defaultdict(list)
    for slot, row in zip(expected_slots(manifest), records, strict=True):
        if any(row[key] != value for key, value in slot.items()):
            raise RuntimeError(f"Journal order differs from the frozen slot {slot}")
        raw, failure = interpret_generation_response(row["http_status"], base64.b64decode(row["raw_body_b64"], validate=True),
                                                     row["transport_error"])
        response = raw.get("response")
        if row["infrastructure_error"] != failure or row["response"] != (response if isinstance(response, str) else None):
            raise RuntimeError(f"Archived response mismatch at {slot}")
        if failure is None:
            parsed = benchmark.parse_llm_response(response)
            if parsed and parsed not in reviewed and (len(parsed) > 1024 or not LEXICAL_SAFETY.fullmatch(parsed)):
                raise RuntimeError(f"Review expression for safe official replay at {slot}: {parsed!r}")
        text = f"Error querying Ollama: {failure}" if failure else response
        trajectories[row["seed"], row["group_id"], row["method"]].append(text)
        verified_records[row["method"]].append({**row, "generation_metadata": raw})
    cells = []
    for (seed, group_id, method), responses in trajectories.items():
        replay = Replay(responses)
        official = benchmark.evaluate_single_observation_set(
            replay, labels[group_id], n_queries=len(responses), verbose=False, max_retries=1,
        )
        if replay.used != len(responses):
            raise RuntimeError("Official replay did not consume one response per slot")
        cells.append({"seed": seed, "group_id": group_id, "method": method, "official": official})
    cell_metrics = {(c["seed"], c["group_id"], c["method"]): c["official"] for c in cells}
    pairs = [{"seed": seed, "group_id": group_id, **{
        metric + "_delta": cell_metrics[seed, group_id, "FSCB"][metric] - official[metric] for metric in METRICS}}
        for (seed, group_id, method), official in cell_metrics.items() if method == "ACB"]
    summary = {method: {metric: mean(c["official"][metric] for c in cells if c["method"] == method)
                        for metric in METRICS} for method in METHODS}
    paired_summary = {}
    for metric in METRICS:
        deltas = [pair[metric + "_delta"] for pair in pairs]
        paired_summary[metric] = {"mean": mean(deltas), "sd": stdev(deltas) if len(deltas) > 1 else None,
                                  "wins": sum(d > 0 for d in deltas), "ties": sum(d == 0 for d in deltas),
                                  "losses": sum(d < 0 for d in deltas)}
    return {"summary": summary, "trajectories": cells, "pairs": pairs, "paired_summary": paired_summary,
            "online_diagnostics": {method: action_summary(verified_records[method]) for method in METHODS},
            "request_cost": {method: request_cost(verified_records[method]) for method in METHODS},
            "requests": len(records), "infrastructure_failures": sum(row["infrastructure_error"] is not None for row in records)}


def score(run_dir):
    manifest, freeze_hash = load_bundle(run_dir)
    if pinned.digest_file(pinned.DATASET) != pinned.DATASET_SHA256:
        raise RuntimeError("The archived answer dataset changed")
    journal = (run_dir / "requests.jsonl").read_bytes()
    if (run_dir / "collection_seal.json").read_bytes() != pinned.json_bytes(collection_seal(journal, freeze_hash)):
        raise RuntimeError("Journal bytes or freeze digest do not match the seal")
    review_path = run_dir / "reviewed-replay-expressions.json"
    reviewed = json.loads(review_path.read_bytes()) if review_path.is_file() else ()
    dataset = json.loads(pinned.DATASET.read_bytes())
    labels = {group["observation_set_id"]: group
              for groups in dataset["datasets_by_n_observations"].values() for group in groups}
    results = score_records(manifest, [json.loads(line) for line in journal.splitlines()], labels, reviewed=reviewed)
    with (run_dir / "official-results.json").open("xb") as output:
        output.write(pinned.json_bytes(results))
    return results["summary"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "preflight", "collect", "score"))
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--authorized", action="store_true", help="Required after separate live-run authorization")
    args = parser.parse_args()
    if args.command == "collect":
        if not args.authorized:
            parser.error("Collect requires separate live-run authorization and --authorized")
        collect_live(args.run_dir)
    elif args.command == "preflight":
        load_bundle(args.run_dir)
        print(json.dumps(pinned.probe_environment(args.run_dir), indent=2))
    else:
        output = prepare(args.run_dir) if args.command == "prepare" else score(args.run_dir)
        print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
