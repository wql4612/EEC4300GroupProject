"""Collect a frozen Boolean Prompt experiment from a public-only manifest."""

import argparse
import base64
import hashlib
import http.client
import json
import os
import re
import sys
from contextlib import ExitStack
from datetime import datetime
from pathlib import Path
from time import perf_counter

from scripts.run_boolean_smoke import is_safe_boolean_expression

D2_VARIANTS = ("P0", "P1", "P2")
P3_VARIANTS = ("P1", "P2", "P3")
SEEDS = (42, 271828, 314159)
MODEL_DIGEST = "357c53fb659c5076de1d65ccb0b397446227b71a42be9d1603d46168015c9e4b"
OPTIONS = {"temperature": 0.6, "num_predict": 128, "num_ctx": 4096}
P3_GROUPS = (
    "n1_inputs_11_outputs_1",
    "n2_inputs_00_11_outputs_01",
    "n2_inputs_00_10_outputs_00",
    "n3_inputs_00_01_11_outputs_011",
    "n3_inputs_01_10_11_outputs_101",
)
P3_PROMPT_HASHES = {
    "P1": "e6b0498c7866f667218ff16f163240d766439e052c84507e5a2a3911d71f36ab",
    "P2": "5811d33e71fdafc0eaf59d10e6d50695480f8cef3959f3096072ba31cad6d265",
    "P3": "3dc9205f284eb45464dd7c70978bf006da3cdaad0ac5cf8b0da9aa206580b2a5",
}
MANIFEST_FIELDS = {"model", "model_digest", "options", "templates", "blocks", "block_rerun_min_failures"}
BLOCK_FIELDS = {"id", "group_id", "observations", "seed", "slot_seeds", "prompt_order"}
LEGACY_FREEZE_FIELDS = {
    "upstream_commit", "dataset_sha256", "split_manifest_sha256", "selection_manifest_sha256",
    "online_manifest_sha256", "collector_sha256", "prompt_hashes", "rollout_seeds", "planned_requests",
    "model", "ollama_version", "model_parameters", "model_template_sha256", "python", "gpu_before", "retry_rule",
}


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def public_observation_text(group_id):
    if not isinstance(group_id, str):
        raise RuntimeError("Group ID must be a public Boolean observation ID")
    match = re.fullmatch(r"n([1-4])_inputs_([01]{2}(?:_[01]{2})*)_outputs_([01]+)", group_id)
    if match is None:
        raise RuntimeError(f"Invalid Group ID: {group_id}")
    count, inputs, outputs = match.groups()
    pairs = inputs.split("_")
    if len(pairs) != int(count) or len(outputs) != int(count) or pairs != sorted(set(pairs)):
        raise RuntimeError(f"Invalid observations in Group ID: {group_id}")
    return "\n".join(f"(x={x}, y={y}) -> {value}" for (x, y), value in zip(pairs, outputs, strict=True))


def validate_public_manifest(config, freeze, variants):
    if not isinstance(config, dict) or set(config) != MANIFEST_FIELDS:
        raise RuntimeError("Unexpected online manifest fields")
    if variants == D2_VARIANTS and (not isinstance(freeze, dict) or set(freeze) - LEGACY_FREEZE_FIELDS):
        raise RuntimeError("Unexpected freeze receipt fields")
    if config["model"] != "qwen2.5:3b" or config["model_digest"] != MODEL_DIGEST:
        raise RuntimeError("Online manifest model differs from the registered model")
    options = config["options"]
    if (
        freeze.get("model", {}).get("digest") != config["model_digest"] or options != OPTIONS
        or any(type(options[key]) is not type(value) for key, value in OPTIONS.items())
    ):
        raise RuntimeError("Online manifest model or generation options differ from the frozen plan")
    if type(config["block_rerun_min_failures"]) is not int or config["block_rerun_min_failures"] != 2:
        raise RuntimeError("Online manifest rerun threshold differs from the frozen plan")
    templates = config["templates"]
    if not isinstance(templates, dict) or set(templates) != set(variants) or not all(
        isinstance(value, str) for value in templates.values()
    ):
        raise RuntimeError("Unexpected Prompt condition or template")
    hashes = {variant: sha256(templates[variant].encode("utf-8")) for variant in variants}
    if hashes != freeze.get("prompt_hashes") or (variants == P3_VARIANTS and hashes != P3_PROMPT_HASHES):
        raise RuntimeError("Prompt templates differ from the frozen hashes")
    if freeze.get("rollout_seeds") != list(SEEDS) or freeze.get("planned_requests") != 180:
        raise RuntimeError("Frozen rollout seeds or 180-slot budget differ from the registered plan")

    blocks = config["blocks"]
    if not isinstance(blocks, list) or len(blocks) != 15:
        raise RuntimeError("Expected 15 blocks and 180 planned slots")
    block_ids = set()
    for block in blocks:
        if not isinstance(block, dict) or set(block) != BLOCK_FIELDS:
            raise RuntimeError("Unexpected Block fields")
        if not isinstance(block["id"], str) or not block["id"] or block["id"] in block_ids:
            raise RuntimeError("Block IDs must be nonempty and unique")
        block_ids.add(block["id"])
        if block["observations"] != public_observation_text(block["group_id"]):
            raise RuntimeError(f"Observations differ from Group ID in block {block['id']}")
    groups = P3_GROUPS if variants == P3_VARIANTS else tuple(block["group_id"] for block in blocks[:5])
    if len(set(groups)) != 5:
        raise RuntimeError("Expected five unique Group IDs")
    for index, block in enumerate(blocks):
        seed_index, group_index = divmod(index, 5)
        seed = SEEDS[seed_index]
        if block["group_id"] != groups[group_index]:
            raise RuntimeError(f"Group ID differs from the frozen plan in block {block['id']}")
        slot_seeds = block["slot_seeds"]
        first_seed = seed + 4 * group_index
        if (
            type(block["seed"]) is not int or block["seed"] != seed
            or not isinstance(slot_seeds, list) or any(type(value) is not int for value in slot_seeds)
            or slot_seeds != list(range(first_seed, first_seed + 4))
        ):
            raise RuntimeError(f"Slot seed mapping differs from the frozen plan in block {block['id']}")
        if variants == P3_VARIANTS and block["id"] != f"{seed}-H{group_index + 1}":
            raise RuntimeError(f"Block ID differs from the frozen plan: {block['id']}")
        rotation = (seed_index + group_index) % len(variants)
        if block["prompt_order"] != list(variants[rotation:] + variants[:rotation]):
            raise RuntimeError(f"Prompt order differs from the frozen plan in block {block['id']}")


def file_lockdown(event, args):
    if (
        event in {"open", "os.listdir", "os.scandir", "os.system", "subprocess.Popen", "_winapi.CreateProcess"}
        or event.startswith(("ctypes.", "os.exec", "os.spawn"))
    ):
        raise PermissionError("D2 collector cannot open files or launch processes after setup")


def post_once(payload):
    connection = http.client.HTTPConnection("127.0.0.1", 11434, timeout=120)
    status = None
    body = b""
    try:
        connection.request(
            "POST", "/api/generate", body=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        response = connection.getresponse()
        status = response.status
        body = response.read()
        return status, body, None
    except (OSError, http.client.HTTPException) as error:
        return status, getattr(error, "partial", body) or body, f"{type(error).__name__}: {error}"
    finally:
        connection.close()


def interpret_generation_response(status, body, transport_error):
    """Keep any decodable response text and independently check completion."""
    try:
        decoded = json.loads(body)
    except (ValueError, UnicodeError):
        decoded = None
    raw = decoded if isinstance(decoded, dict) else {}

    if transport_error:
        return raw, transport_error
    if status != 200:
        return raw, f"HTTP {status}"
    if not isinstance(decoded, dict):
        return raw, "Ollama response body is not a complete JSON object"
    if raw.get("error"):
        return raw, "Ollama reported a generation error"
    if raw.get("done") is not True or not isinstance(raw.get("response"), str):
        return raw, "Ollama did not return a completed response"
    return raw, None


def render(template, observations, history):
    blocks = {
        "{{OBSERVATIONS}}": observations,
        "{{PRIOR_EXPRESSIONS}}": "\n".join(f"Expression: {value}" for value in history) or "None",
    }
    return re.sub(r"\{\{(?:OBSERVATIONS|PRIOR_EXPRESSIONS)\}\}", lambda match: blocks[match[0]], template)


def collect(config, log, post, parse, guard):
    for block in config["blocks"]:
        for attempt in (1, 2):
            histories = {variant: [] for variant in block["prompt_order"]}
            failures = 0
            for slot, request_seed in enumerate(block["slot_seeds"], 1):
                for variant in block["prompt_order"]:
                    prompt = render(config["templates"][variant], block["observations"], histories[variant])
                    payload = {
                        "model": config["model"], "prompt": prompt, "stream": False,
                        "options": {**config["options"], "seed": request_seed},
                    }
                    started_at = datetime.now().astimezone().isoformat()
                    start = perf_counter()
                    status, body, transport_error = post(payload)
                    elapsed = perf_counter() - start
                    raw, failure = interpret_generation_response(status, body, transport_error)
                    text = raw.get("response")
                    if not isinstance(text, str):
                        text = None
                    parsed = parse(text) if failure is None else None
                    if parsed:
                        histories[variant].append(parsed)
                    entry = {
                        "block": block["id"], "group_id": block["group_id"], "paired_seed": block["seed"],
                        "attempt": attempt, "variant": variant, "slot": slot, "started_at": started_at,
                        "request": payload, "model_digest": config["model_digest"],
                        "prompt_sha256": sha256(prompt.encode("utf-8")),
                        "template_sha256": sha256(config["templates"][variant].encode("utf-8")),
                        "http_status": status, "raw_body_b64": base64.b64encode(body).decode("ascii"),
                        "response": text, "parsed_expression": parsed, "guard_pass": guard(parsed),
                        "transport_error": transport_error, "infrastructure_error": failure,
                        "prompt_eval_count": raw.get("prompt_eval_count"), "eval_count": raw.get("eval_count"),
                        "elapsed_seconds": elapsed,
                    }
                    log.write(json.dumps(entry, ensure_ascii=False) + "\n")
                    log.flush()
                    failures += failure is not None
            print(f"{block['id']} attempt {attempt}: {failures} infrastructure failures", flush=True)
            if failures < config["block_rerun_min_failures"]:
                break


def read_legacy_d2(run_dir, probe_only):
    config_bytes = (run_dir / "online_manifest.json").read_bytes()
    receipt = json.loads((run_dir / "freeze.json").read_bytes())
    manifest_hash = sha256(config_bytes)
    if manifest_hash != receipt["online_manifest_sha256"]:
        raise SystemExit("Frozen online manifest changed")
    if sha256(Path(__file__).read_bytes()) != receipt["collector_sha256"]:
        raise SystemExit("Frozen collector changed")
    config = json.loads(config_bytes)
    validate_public_manifest(config, receipt, D2_VARIANTS)
    if not probe_only:
        preflight_path = run_dir / "preflight.json"
        if not preflight_path.is_file():
            raise SystemExit("Preflight receipt is required")
        preflight = json.loads(preflight_path.read_bytes())
        if preflight.get("online_manifest_sha256") != manifest_hash:
            raise SystemExit("Preflight receipt does not match the current online manifest")
    return config


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--probe-only", action="store_true")
    parser.add_argument("--p3", action="store_true", help="Require the registered P1/P2/P3 validation plan")
    args = parser.parse_args()
    if args.p3:
        from scripts import p3_collection_seal, p3_preflight

        config, _, bindings = p3_preflight.read_bundle(args.run_dir, online=True)
        if not args.probe_only and any((args.run_dir / name).exists() for name in p3_preflight.OUTPUT_NAMES):
            raise RuntimeError("Output directory already contains a journal or a scoring result")
    else:
        config = read_legacy_d2(args.run_dir, args.probe_only)

    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root.parent / "HypoSpace" / "boolean"))
    from boolean_benchmark import BooleanBenchmarkRefined

    parse = BooleanBenchmarkRefined.__new__(BooleanBenchmarkRefined).parse_llm_response
    probe = root / "data" / "d1" / "boolean_basic_depth2.json"
    if not probe.is_file():
        raise SystemExit("Expected a real local answer file for the deny-read probe")
    with ExitStack() as resources:
        log = sys.stdout
        pending = None
        if not args.probe_only:
            mode = "x+" if args.p3 else "x"
            log = resources.enter_context((args.run_dir / "requests.jsonl").open(mode, encoding="utf-8", newline="\n"))
            if args.p3:
                pending_path = args.run_dir / p3_collection_seal.PENDING_SEAL
                pending = resources.enter_context(pending_path.open("xb"))
        sys.addaudithook(file_lockdown)
        try:
            with probe.open("rb"):
                raise RuntimeError("Answer file readable after lockdown")
        except PermissionError:
            print("Data lockdown verified: existing answer file denied", flush=True)
        if not args.probe_only:
            collect(config, log, post_once, parse, is_safe_boolean_expression)
        if pending is not None:
            log.flush()
            os.fsync(log.fileno())
            log.seek(0)
            seal = p3_collection_seal.make_receipt(log.buffer.read(), bindings)
            pending.write(p3_preflight.json_bytes(seal))
            pending.flush()
            os.fsync(pending.fileno())
    if pending is not None:
        # Publish the closed receipt atomically and refuse to overwrite another seal.
        os.link(pending_path, args.run_dir / p3_collection_seal.SEAL)
        pending_path.unlink()


if __name__ == "__main__":
    main()
