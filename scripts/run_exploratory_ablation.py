"""Prepare and run a separate, matched FSCB-Hypo reward ablation."""

import argparse
import base64
import io
import json
import os
import sys
from collections import defaultdict
from dataclasses import asdict
from pathlib import Path
from statistics import mean, stdev
from time import perf_counter

from scripts import run_exploratory as primary
from scripts.run_exploratory import pinned
from scripts.fscb_hypo import Controller

METHOD = "FSCB-no-bonus"
PLANNED = 48
BETA = 0.
PENDING = ".collection_seal.incomplete"


def matched_slots(manifest):
    for index, slot in enumerate(
        (slot for slot in primary.expected_slots(manifest) if slot["method"] == "FSCB"), 1,
    ):
        yield {**slot, "method": METHOD, "index": index, "primary_index": slot["index"]}


def bundle_bytes(primary_dir):
    main_manifest, main_hash = primary.load_bundle(primary_dir)
    slots = list(matched_slots(main_manifest))
    if len(slots) != PLANNED:
        raise RuntimeError("Expected 48 matched FSCB slots")
    manifest = pinned.json_bytes({"schema_version": 1, "condition": METHOD, "slots": slots, "base": main_manifest})
    freeze = pinned.json_bytes({
        "schema_version": 1, "condition": METHOD, "planned_requests": PLANNED, "beta": BETA,
        "primary_freeze_sha256": main_hash, "online_manifest_sha256": primary.sha256(manifest),
        "ablation_source_sha256": pinned.digest_file(Path(__file__)),
    })
    return main_manifest, manifest, freeze


def prepare(primary_dir, run_dir):
    if run_dir.exists():
        raise FileExistsError(f"Use a new ablation directory: {run_dir}")
    _, manifest, freeze = bundle_bytes(primary_dir)
    run_dir.mkdir(parents=True, exist_ok=False)
    (run_dir / "online_manifest.json").write_bytes(manifest)
    (run_dir / "freeze.json").write_bytes(freeze)
    return {"planned_requests": PLANNED, "freeze_sha256": primary.sha256(freeze),
            "online_manifest_sha256": primary.sha256(manifest)}


def load_bundle(primary_dir, run_dir):
    manifest, manifest_bytes, freeze_bytes = bundle_bytes(primary_dir)
    if (run_dir / "online_manifest.json").read_bytes() != manifest_bytes:
        raise RuntimeError("The ablation manifest differs from the exact frozen FSCB slots")
    if (run_dir / "freeze.json").read_bytes() != freeze_bytes:
        raise RuntimeError("The ablation freeze differs from the frozen configuration")
    return manifest, primary.sha256(freeze_bytes)


def collect(manifest, journal, post):
    blocks = {(block["seed"], block["group_id"]): block for block in manifest["blocks"]}
    policies = {seed: Controller("FSCB", seed, beta=BETA) for seed in {seed for seed, _ in blocks}}
    for slot in matched_slots(manifest):
        policy = policies[slot["seed"]]
        block = blocks[slot["seed"], slot["group_id"]]
        if slot["slot"] == 1:
            policy.start_group(primary.public_rows(block["observations"]), seed=block["slot_seeds"][0])
        decision = policy.decide(slot["slot"] - 1, len(block["slot_seeds"]))
        prompt = primary.render_prompt(manifest["template"], block["observations"], decision)
        request = {
            "model": manifest["model"], "prompt": prompt, "stream": False,
            "options": {**manifest["options"], "seed": slot["model_seed"], "temperature": decision.arm.temperature},
        }
        started = perf_counter()
        http_status, body, transport_error = post(request)
        elapsed = perf_counter() - started
        raw, failure = primary.interpret_generation_response(http_status, body, transport_error)
        text = raw.get("response")
        feedback = policy.observe(decision, text if failure is None else None)
        record = {**slot, "decision": asdict(decision), "feedback": feedback, "request": request,
                  "http_status": http_status, "transport_error": transport_error, "infrastructure_error": failure,
                  "raw_body_b64": base64.b64encode(body).decode("ascii"),
                  "response": text if isinstance(text, str) else None, "elapsed_seconds": elapsed,
                  "prompt_eval_count": raw.get("prompt_eval_count"), "eval_count": raw.get("eval_count")}
        journal.write(json.dumps(record, ensure_ascii=False) + "\n")
        journal.flush()


def seal(content, freeze_hash):
    if not content.endswith(b"\n") or len(content.splitlines()) != PLANNED:
        raise RuntimeError("Only a complete 48-slot ablation can be sealed")
    return pinned.json_bytes({"requests_sha256": primary.sha256(content), "bytes": len(content), "records": PLANNED,
                              "freeze_sha256": freeze_hash})


def collect_live(primary_dir, run_dir):
    manifest, freeze_hash = load_bundle(primary_dir, run_dir)
    if not all((primary_dir / name).is_file() for name in ("collection_seal.json", "official-results.json")):
        raise RuntimeError("Complete and score the 96-slot primary comparison before the ablation")
    if any((run_dir / name).exists() for name in primary.OUTPUTS):
        raise FileExistsError("Ablation directory already contains experiment outputs")
    environment = pinned.probe_environment(run_dir)
    (run_dir / "launch-environment.json").write_bytes(pinned.json_bytes(environment))
    pending = run_dir / PENDING
    with (run_dir / "requests.jsonl").open("x+", encoding="utf-8", newline="\n") as journal, pending.open("xb") as receipt:
        sys.addaudithook(primary.file_lockdown)
        try:
            pinned.DATASET.open("rb").close()
        except PermissionError:
            pass
        else:
            raise RuntimeError("The online process can still read the answer-bearing dataset")
        collect(manifest, journal, primary.post_once)
        journal.flush()
        os.fsync(journal.fileno())
        journal.seek(0)
        receipt.write(seal(journal.buffer.read(), freeze_hash))
        receipt.flush()
        os.fsync(receipt.fileno())
    os.link(pending, run_dir / "collection_seal.json")
    pending.unlink()


def replay(manifest, records, labels, *, reviewed=()):
    """Verify the no-bonus policy trace, then use the unchanged official evaluator."""
    from modules.llm_interface import LLMInterface
    from scripts.candidate_history import BooleanBenchmarkRefined

    source = iter(records)

    def transport(request):
        stored = next(source)
        if request != stored["request"]:
            raise RuntimeError(f"Ablation trace differs at request {stored['index']}")
        return stored["http_status"], base64.b64decode(stored["raw_body_b64"], validate=True), stored["transport_error"]

    regenerated = io.StringIO()
    collect(manifest, regenerated, transport)
    for generated, stored in zip(regenerated.getvalue().splitlines(), records, strict=True):
        if any(stored.get(key) != value for key, value in json.loads(generated).items() if key != "elapsed_seconds"):
            raise RuntimeError(f"Ablation trace differs at response {stored['index']}")

    class Replay(LLMInterface):
        def __init__(self, values):
            self.responses = iter(values)

        def query(self, prompt):
            return next(self.responses)

        def get_name(self):
            return "Sealed no-bonus replay"

    benchmark = BooleanBenchmarkRefined.__new__(BooleanBenchmarkRefined)
    benchmark.variables, benchmark.operators, benchmark.max_depth = ["x", "y"], {"AND", "OR"}, 2
    benchmark.mechanistic_opts = dict.fromkeys(
        ("apply_commutativity", "apply_idempotence_and_or", "flatten_associativity"), True,
    )
    benchmark.complete_dataset = {"metadata": {"mechanistic_opts": benchmark.mechanistic_opts}}
    trajectories = defaultdict(list)
    for row in records:
        failure = row["infrastructure_error"]
        response = f"Error querying Ollama: {failure}" if failure else row["response"]
        parsed = benchmark.parse_llm_response(response) if not failure else None
        if parsed and parsed not in reviewed and (len(parsed) > 1024 or not primary.LEXICAL_SAFETY.fullmatch(parsed)):
            raise RuntimeError(f"Review expression for safe official replay at ablation slot {row['index']}: {parsed!r}")
        trajectories[row["seed"], row["group_id"]].append(response)

    scored = []
    for (seed, group_id), responses in trajectories.items():
        player = Replay(responses)
        official = benchmark.evaluate_single_observation_set(
            player, labels[group_id], n_queries=len(responses), verbose=False, max_retries=1,
        )
        if next(player.responses, None) is not None:
            raise RuntimeError("Official replay did not consume all ablation responses")
        scored.append({"seed": seed, "group_id": group_id, "official": official})
    return scored


def score(primary_dir, run_dir):
    manifest, freeze_hash = load_bundle(primary_dir, run_dir)
    run_bytes = (run_dir / "requests.jsonl").read_bytes()
    if (run_dir / "collection_seal.json").read_bytes() != seal(run_bytes, freeze_hash):
        raise RuntimeError("Ablation journal does not match its seal")
    _, main_hash = primary.load_bundle(primary_dir)
    main_bytes = (primary_dir / "requests.jsonl").read_bytes()
    main_seal = pinned.json_bytes(primary.collection_seal(main_bytes, main_hash))
    if (primary_dir / "collection_seal.json").read_bytes() != main_seal:
        raise RuntimeError("Primary journal does not match its seal")
    if pinned.digest_file(pinned.DATASET) != pinned.DATASET_SHA256:
        raise RuntimeError("Archived answer dataset changed")
    dataset = json.loads(pinned.DATASET.read_bytes())
    labels = {group["observation_set_id"]: group
              for groups in dataset["datasets_by_n_observations"].values() for group in groups}

    def review(directory):
        path = directory / "reviewed-replay-expressions.json"
        return json.loads(path.read_bytes()) if path.is_file() else ()

    main_records = [json.loads(line) for line in main_bytes.splitlines()]
    full = primary.score_records(manifest, main_records, labels, reviewed=review(primary_dir))
    if pinned.json_bytes(full) != (primary_dir / "official-results.json").read_bytes():
        raise RuntimeError("The primary official results differ from sealed replay")
    full_cells = {(row["seed"], row["group_id"]): row["official"]
                  for row in full["trajectories"] if row["method"] == "FSCB"}
    records = [json.loads(line) for line in run_bytes.splitlines()]
    cells = replay(manifest, records, labels, reviewed=review(run_dir))
    if {(row["seed"], row["group_id"]) for row in cells} != set(full_cells):
        raise RuntimeError("Missing a matched full FSCB trajectory")
    pairs = []
    for row in cells:
        seed, group_id = row["seed"], row["group_id"]
        pairs.append({"seed": seed, "group_id": group_id, **{
            metric + "_full_minus_no_bonus": full_cells[seed, group_id][metric] - row["official"][metric]
            for metric in primary.METRICS}})
    summary = {}
    for metric in primary.METRICS:
        column = [row[metric + "_full_minus_no_bonus"] for row in pairs]
        summary[metric] = {"full": mean(cell[metric] for cell in full_cells.values()),
                           "no_bonus": mean(cell["official"][metric] for cell in cells),
                           "mean_delta": mean(column), "sd_delta": stdev(column),
                           "positive": sum(x > 0 for x in column), "ties": sum(x == 0 for x in column),
                           "negative": sum(x < 0 for x in column)}
    result = {"summary": summary, "pairs": pairs, "trajectories": cells, "requests": PLANNED,
              "infrastructure_failures": sum(row["infrastructure_error"] is not None for row in records),
              "online_diagnostics": primary.action_summary(records),
              "limitation": "The ablation was collected in a separate execution period."}
    with (run_dir / "official-results.json").open("xb") as output:
        output.write(pinned.json_bytes(result))
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "collect", "score"))
    parser.add_argument("primary_dir", type=Path)
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--authorized", action="store_true", help="Required after separate ablation authorization")
    args = parser.parse_args()
    if args.command == "collect":
        if not args.authorized:
            parser.error("Ablation collection requires separate live-run authorization and --authorized")
        collect_live(args.primary_dir, args.run_dir)
    else:
        command = prepare if args.command == "prepare" else score
        print(json.dumps(command(args.primary_dir, args.run_dir), indent=2))


if __name__ == "__main__":
    main()
