"""Replay the frozen D2 journals with the unmodified official evaluator."""

import argparse
import base64
import csv
import json
import math
import re
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

from scripts.prepare_d2_prompt import DATASET, UPSTREAM, VARIANTS, command, document
from scripts.run_boolean_smoke import is_safe_boolean_expression
from scripts.run_d2_prompt import P3_VARIANTS, interpret_generation_response, render, sha256, validate_public_manifest

METRICS = (
    "parse_success_rate", "guard_pass_rate", "in_space_rate", "valid_rate", "novelty_rate",
    "recovery_rate", "first_valid_novel_discovery_rate", "not_nor_rate", "p2_example_key_hit_rate",
)
LEXICAL_SAFETY = re.compile(r"(?:\b(?:x|y|z|AND|OR|NOT|NOR|XOR|True|False)\b|[01&|~^(), \t])+")
DIAGNOSTICS = {"ast_depth_violation": "ast_depth_violation_rate", "duplicate_mechanistic_key": "duplicate_rate"}


def resolve_request(row, *, strict=False):
    """Classify the sealed response without altering the request journal."""
    body = base64.b64decode(row["raw_body_b64"], validate=True)
    raw, failure = interpret_generation_response(row["http_status"], body, row.get("transport_error"))
    decoded_text = raw.get("response")
    archived_text = row["response"]
    if strict:
        if archived_text != (decoded_text if isinstance(decoded_text, str) else None):
            raise RuntimeError("Stored response differs from the raw HTTP body")
        if failure != row["infrastructure_error"]:
            raise RuntimeError("Stored infrastructure status differs from the raw HTTP response")
        if failure is not None and (row["parsed_expression"] is not None or row["guard_pass"] is not False):
            raise RuntimeError("An infrastructure failure has archived parsed history or Guard Pass")
    else:
        failure = failure or row["infrastructure_error"]
        if failure is None or (isinstance(decoded_text, str) and isinstance(archived_text, str)):
            if decoded_text != archived_text:
                raise RuntimeError("Stored response differs from the raw HTTP body")
    return {**row, "infrastructure_error": failure, "generation_metadata": raw}


def replay_text(row):
    if row["infrastructure_error"]:
        return f"Error querying Ollama: {row['infrastructure_error']}"
    return row["response"]


def checked_journal(run_dir, manifest, freeze, *, variants=VARIANTS, selection_file="split_manifest.json",
                    journal_bytes=None):
    if (
        set(manifest["templates"]) != set(variants)
        or set(freeze["prompt_hashes"]) != set(variants)
        or any(sorted(block["prompt_order"]) != sorted(variants) for block in manifest["blocks"])
    ):
        raise RuntimeError(f"Frozen condition set must be {', '.join(variants)}")
    if sha256((run_dir / "online_manifest.json").read_bytes()) != freeze["online_manifest_sha256"]:
        raise RuntimeError("Online manifest does not match the freeze receipt")
    selection_hash = f"{Path(selection_file).stem}_sha256"
    if sha256((run_dir / selection_file).read_bytes()) != freeze[selection_hash]:
        raise RuntimeError(f"{selection_file} does not match the freeze receipt")
    if sha256(DATASET.read_bytes()) != freeze["dataset_sha256"]:
        raise RuntimeError("Offline answer dataset does not match the freeze receipt")
    validate_public_manifest(manifest, freeze, variants)
    sys.path.insert(0, str(UPSTREAM / "boolean"))
    from boolean_benchmark import BooleanBenchmarkRefined

    parse = BooleanBenchmarkRefined.__new__(BooleanBenchmarkRefined).parse_llm_response
    records = []
    if journal_bytes is None:
        journal_bytes = (run_dir / "requests.jsonl").read_bytes()
    journal = journal_bytes.decode("utf-8")
    lines = journal.removesuffix("\n").split("\n") if variants == P3_VARIANTS else journal.splitlines()
    for line in lines:
        row = json.loads(line)
        try:
            records.append(resolve_request(row, strict=variants == P3_VARIANTS))
        except (RuntimeError, ValueError) as error:
            raise RuntimeError(
                f"{error} at {row['block']}, attempt {row['attempt']}, slot {row['slot']}, {row['variant']}"
            ) from error
    attempts = defaultdict(list)
    for record in records:
        attempts[record["block"], record["attempt"]].append(record)
    chosen = []
    expected_order = []
    reruns = 0
    for block in manifest["blocks"]:
        block_id = block["id"]
        first = attempts.get((block_id, 1), [])
        needs_rerun = sum(bool(row["infrastructure_error"]) for row in first) >= manifest["block_rerun_min_failures"]
        for attempt in range(1, 3 if needs_rerun else 2):
            rows = attempts[block_id, attempt]
            expected = [(slot, variant, seed) for slot, seed in enumerate(block["slot_seeds"], 1)
                        for variant in block["prompt_order"]]
            expected_order.extend((block_id, attempt, slot, variant) for slot, variant, _ in expected)
            if len(rows) != len(expected):
                raise RuntimeError(f"Incomplete attempt {block_id}, {attempt}")
            histories = {variant: [] for variant in variants}
            for row, (slot, variant, seed) in zip(rows, expected):
                payload = row["request"]
                prompt = render(manifest["templates"][variant], block["observations"], histories[variant])
                if (
                    (row["slot"], row["variant"], row["group_id"], row["paired_seed"])
                    != (slot, variant, block["group_id"], block["seed"])
                    or payload != {"model": manifest["model"], "stream": False, "prompt": prompt,
                                   "options": {**manifest["options"], "seed": seed}}
                    or row["model_digest"] != freeze["model"]["digest"]
                    or row["prompt_sha256"] != sha256(prompt.encode("utf-8"))
                    or row["template_sha256"] != freeze["prompt_hashes"][variant]
                ):
                    raise RuntimeError(f"Request differs from frozen plan at {block_id}, {attempt}, {slot}, {variant}")
                parsed = None
                if not row["infrastructure_error"]:
                    parsed = parse(row["response"])
                    if parsed != row["parsed_expression"]:
                        raise RuntimeError(
                            f"Parsed expression differs from the official Parser at {block_id}, "
                            f"attempt {attempt}, slot {slot}, {variant}"
                        )
                    if parsed:
                        histories[variant].append(parsed)
                if variants == P3_VARIANTS:
                    guard = is_safe_boolean_expression(parsed)
                    if row["guard_pass"] is not guard:
                        raise RuntimeError(
                            f"Archived Guard Pass differs from recomputation at {block_id}, "
                            f"attempt {attempt}, slot {slot}, {variant}"
                        )
                    row["guard_pass"] = guard
        chosen.extend(attempts[block_id, 2 if needs_rerun else 1])
        reruns += needs_rerun
    if expected_order != [(r["block"], r["attempt"], r["slot"], r["variant"]) for r in records]:
        raise RuntimeError("Unexpected HTTP request, retry or slot order")
    if len(chosen) != freeze["planned_requests"]:
        raise RuntimeError("Analysis does not cover all planned slots")
    return records, chosen, reruns


def endpoint_metrics(records):
    n_http = sum(r["http_status"] is not None and 200 <= r["http_status"] < 300 for r in records)
    n_completed = sum(r["infrastructure_error"] is None for r in records)
    return {"actual_requests": len(records), "http_2xx": n_http, "completed_responses": n_completed}


def mean(records, field):
    return statistics.mean(record[field] for record in records)


def structural_diagnostics(benchmark, expression_type, parsed, space_seen):
    tree = expression_type(parsed, benchmark.variables, benchmark.operators).sympy_expr if parsed else None
    depth = benchmark._get_ast_depth(tree) if tree is not None else None
    in_space = bool(parsed and benchmark._in_space(parsed))
    key = benchmark.get_expression_mechanistic_key(parsed) if in_space else None
    duplicate = key is not None and key in space_seen
    if key is not None:
        space_seen.add(key)
    return {
        "in_space": in_space, "mechanistic_key": key, "ast_depth": depth,
        "ast_depth_violation": depth is not None and depth > 2,
        "duplicate_mechanistic_key": duplicate,
    }


def diagnostic_rates(slots):
    return {rate: mean(slots, flag) for flag, rate in DIAGNOSTICS.items()}


def timed_seconds(rows):
    return [
        value for row in rows if type(value := row.get("elapsed_seconds")) in (int, float)
        and math.isfinite(value) and value >= 0
    ]


def request_cost(rows):
    def measured_count(row, field):
        value = row["generation_metadata"].get(field)
        return value if type(value) is int and value >= 0 else None

    token_pairs = [(measured_count(row, "prompt_eval_count"), measured_count(row, "eval_count")) for row in rows]
    inputs = [value for value, _ in token_pairs if value is not None]
    outputs = [value for _, value in token_pairs if value is not None]
    valid_tokens = sum(input_count is not None and output_count is not None for input_count, output_count in token_pairs)
    elapsed = timed_seconds(rows)
    return {
        "requests": len(rows),
        "input_tokens": sum(inputs) if inputs else None,
        "output_tokens": sum(outputs) if outputs else None,
        "input_token_records": len(inputs),
        "output_token_records": len(outputs),
        "token_valid_records": valid_tokens,
        "token_missing_records": len(rows) - valid_tokens,
        "mean_request_seconds": statistics.mean(elapsed) if elapsed else None,
        "timing_valid_records": len(elapsed),
    }


def passing_prompts(summary, cells, variants):
    passing = []
    for variant in variants:
        coverage = Counter(cell["seed"] for cell in cells if cell["variant"] == variant and cell["valid_rate"] > 0)
        if summary[variant]["valid_rate"] >= 0.25 and sum(n >= 4 for n in coverage.values()) >= 2:
            passing.append(variant)
    return passing


def pick_prompt(summary, cells):
    passing = passing_prompts(summary, cells, ("P1", "P2"))
    if len(passing) == 2:
        new_rate = "first_valid_novel_discovery_rate"
        if abs(summary["P1"][new_rate] - summary["P2"][new_rate]) <= 0.05 + 1e-12:
            passing.sort(key=lambda p: (-summary[p]["in_space_rate"], p))
        else:
            passing.sort(key=lambda p: -summary[p][new_rate])
    return passing[0] if passing else None, passing


def main(*, variants=VARIANTS, selection_file="split_manifest.json"):
    parser = argparse.ArgumentParser(description=f"Score {', '.join(variants)} from sealed journals without model calls.")
    parser.add_argument("run_dir", type=Path)
    run_dir = parser.parse_args().run_dir
    csv_path = run_dir / "group-seed-metrics.csv"
    result_path = run_dir / "official-results.json"
    if csv_path.exists() or result_path.exists():
        raise FileExistsError("Scoring outputs already exist. Use an offline copy of the sealed inputs.")
    journal_bytes = seal = None
    if variants == P3_VARIANTS:
        from scripts import p3_collection_seal, p3_preflight

        manifest, freeze, bindings = p3_preflight.read_bundle(run_dir, online=False)
        journal_bytes, seal = p3_collection_seal.verify_collection(run_dir, bindings)
    else:
        freeze = json.loads((run_dir / "freeze.json").read_bytes())
        manifest = json.loads((run_dir / "online_manifest.json").read_bytes())
        if command("git", "-C", str(UPSTREAM), "rev-parse", "HEAD").strip() != freeze["upstream_commit"]:
            raise RuntimeError("Offline scorer is not on the pinned upstream commit")
        if command("git", "-C", str(UPSTREAM), "status", "--porcelain").strip():
            raise RuntimeError("Offline scorer checkout has local edits")
        preflight = json.loads((run_dir / "preflight.json").read_bytes())
        if preflight["online_manifest_sha256"] != freeze["online_manifest_sha256"]:
            raise RuntimeError("Archived D2 Preflight does not match the frozen online manifest")
    records, chosen, reruns = checked_journal(
        run_dir, manifest, freeze, variants=variants, selection_file=selection_file, journal_bytes=journal_bytes,
    )
    from boolean_benchmark import BooleanBenchmarkRefined
    from boolean_dataset import BooleanExpression, BooleanObservation
    from modules.llm_interface import LLMInterface

    class Replay(LLMInterface):
        def __init__(self, raw_responses):
            self.responses = iter(raw_responses)

        def get_name(self):
            return "D2 original response replay"

        def query(self, prompt):
            return next(self.responses)

    benchmark = BooleanBenchmarkRefined(str(DATASET))
    examples = {benchmark.get_expression_mechanistic_key(expr) for expr in ("x AND y", "x OR y")}
    groups = {g["observation_set_id"]: g for g in benchmark.all_observation_sets}
    cells = []
    slots = []
    by_variant_block = defaultdict(list)
    for record in chosen:
        by_variant_block[record["variant"], record["block"]].append(record)
    # This check protects the offline evaluator's SymPy string input. It never
    # substitutes a different response or issues a partial official score.
    review_path = run_dir / "reviewed-replay-expressions.json"
    reviewed = set(json.loads(review_path.read_bytes())) if review_path.is_file() else set()
    responses_needing_review = set()
    for row in chosen:
        if row["infrastructure_error"]:
            continue
        parsed = row["parsed_expression"]
        if parsed and parsed not in reviewed and (len(parsed) > 1024 or not LEXICAL_SAFETY.fullmatch(parsed)):
            responses_needing_review.add(parsed)
    if responses_needing_review:
        raise RuntimeError(f"Review archived expressions before offline replay: {sorted(responses_needing_review)!r}")

    for (variant, block), rows in sorted(by_variant_block.items()):
        group = groups[rows[0]["group_id"]]
        observations = [BooleanObservation(row["inputs"], row["output"]) for row in group["observations"]]
        raw_responses = [replay_text(row) for row in rows]
        replay = Replay(raw_responses)
        official = benchmark.evaluate_single_observation_set(
            replay, group, n_queries=len(rows), verbose=False, max_retries=1
        )
        if next(replay.responses, None) is not None:
            raise RuntimeError("Official replay did not consume the complete block")
        per_slot = []
        valid_seen = set()
        space_seen = set()
        for row, raw in zip(rows, raw_responses):
            parsed = None if raw.startswith("Error querying") else row["parsed_expression"]
            structural = structural_diagnostics(benchmark, BooleanExpression, parsed, space_seen)
            key = structural["mechanistic_key"]
            valid = bool(parsed and benchmark.validate_expression(parsed, observations)[0])
            first_valid_new = valid and key is not None and key not in valid_seen
            if first_valid_new:
                valid_seen.add(key)
            per_slot.append({
                "variant": variant, "block": block, "seed": row["paired_seed"], "group_id": row["group_id"],
                "slot": row["slot"], "attempt": row["attempt"], "parsed": parsed, **structural,
                "official_valid": valid, "first_valid_new": first_valid_new,
                "guard_pass": not row["infrastructure_error"] and row["guard_pass"],
                "infrastructure_error": row["infrastructure_error"],
                "not_nor": bool(re.search(r"\b(?:NOT|NOR)\b", row["response"] or "", re.IGNORECASE)),
                "p2_example_key_hit": key in examples,
            })
        n_slots = len(rows)
        if (mean(per_slot, "official_valid") != official["valid_rate"]
                or mean(per_slot, "in_space") != official["in_space_rate"]
                or sum(bool(s["parsed"]) for s in per_slot) / n_slots != official["parse_success_rate"]
                or len(space_seen) != official["n_unique_all"]
                or len(valid_seen) != official["n_unique_valid"]):
            raise RuntimeError("Slot diagnostics disagree with the official replay")
        slots.extend(per_slot)
        cells.append({
            "variant": variant, "block": block, "seed": rows[0]["paired_seed"], "group_id": rows[0]["group_id"],
            **{key: official[key] for key in ("parse_success_rate", "in_space_rate", "valid_rate", "novelty_rate", "recovery_rate")},
            "guard_pass_rate": mean(per_slot, "guard_pass"),
            "first_valid_novel_discovery_rate": mean(per_slot, "first_valid_new"),
            "not_nor_rate": mean(per_slot, "not_nor"), "p2_example_key_hit_rate": mean(per_slot, "p2_example_key_hit"),
            "first_valid_slot": next((s["slot"] for s in per_slot if s["official_valid"]), None),
            "first_valid_new_slot": next((s["slot"] for s in per_slot if s["first_valid_new"]), None),
            "diagnostics": diagnostic_rates(per_slot),
            "official": official,
        })

    summary = {}
    for variant in variants:
        variant_cells = [cell for cell in cells if cell["variant"] == variant]
        actual = [row for row in records if row["variant"] == variant]
        analysed = [row for row in chosen if row["variant"] == variant]
        selected_cost = request_cost(analysed)
        timings = timed_seconds(row for row in analysed if not row["infrastructure_error"])
        bodies = [row["generation_metadata"] for row in analysed if not row["infrastructure_error"]]
        summary[variant] = {
            **endpoint_metrics(actual), **{field: mean(variant_cells, field) for field in METRICS},
            "first_valid_groups": sum(cell["first_valid_slot"] is not None for cell in variant_cells),
            "first_valid_new_groups": sum(cell["first_valid_new_slot"] is not None for cell in variant_cells),
            "http_median_seconds": statistics.median(timings) if timings else None,
            "http_p95_seconds": sorted(timings)[max(0, (95 * len(timings) + 99) // 100 - 1)] if timings else None,
            "ollama_total_mean_seconds": statistics.mean(b.get("total_duration", 0) for b in bodies) / 1e9 if bodies else None,
            "prompt_tokens": selected_cost["input_tokens"],
            "completion_tokens": selected_cost["output_tokens"],
            "diagnostics": diagnostic_rates([slot for slot in slots if slot["variant"] == variant]),
            "request_cost": {"selected_attempts": selected_cost, "all_attempts": request_cost(actual)},
        }
    if variants == VARIANTS:
        recommendation, passing = pick_prompt(summary, cells)
    else:
        recommendation, passing = None, passing_prompts(summary, cells, variants)
    headers = ["variant", "seed", "group_id", *METRICS, *DIAGNOSTICS.values(),
               "first_valid_slot", "first_valid_new_slot", "block"]
    with csv_path.open("x", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=headers, extrasaction="ignore")
        writer.writeheader()
        writer.writerows({**cell, **cell["diagnostics"]} for cell in cells)
    result = {
        "planned_requests": freeze["planned_requests"], "http": endpoint_metrics(records), "rerun_blocks": reruns,
        "rerun_requests": len(records) - freeze["planned_requests"], "prompt_hashes": freeze["prompt_hashes"],
        "summary": summary, "by_group_seed": cells, "by_slot": slots, "recommended_prompt": recommendation,
        "passes_quality_gate": passing,
    }
    if seal is not None:
        result["collection_seal"] = seal
    with result_path.open("x", encoding="utf-8", newline="\n") as file:
        json.dump(result, file, indent=2, ensure_ascii=False)
        file.write("\n")
    print(json.dumps({"summary": summary, "recommendation": recommendation, "passes": passing, "reruns": reruns}, indent=2))


if __name__ == "__main__":
    main()
