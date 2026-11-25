import argparse
import ast
import hashlib
import json
import platform
import re
import subprocess
import sys
from importlib.metadata import version
from itertools import count
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
OLLAMA = "http://127.0.0.1:11434"
DEPENDENCIES = ("networkx", "numpy", "pyyaml", "requests", "scipy", "sympy")


def is_safe_boolean_expression(expression):
    # Restrict inputs before the benchmark passes them to SymPy's string parser.
    if not expression or len(expression) > 1024:
        return False
    translated = expression.replace("AND", "&").replace("OR", "|").replace("NOT", "~")
    try:
        tree = ast.parse(translated.strip(), mode="eval")
    except SyntaxError:
        return False
    allowed_nodes = (
        ast.Expression, ast.Name, ast.Load, ast.BinOp, ast.BitAnd, ast.BitOr,
        ast.UnaryOp, ast.Invert,
    )
    return all(
        isinstance(node, allowed_nodes)
        and (not isinstance(node, ast.Name) or node.id in {"x", "y"})
        for node in ast.walk(tree)
    ) and re.fullmatch(r"[xy&|~() \t]+", translated) is not None


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description="Run the official Boolean benchmark with local Ollama.")
    parser.add_argument("--upstream", type=Path, default=ROOT.parent / "HypoSpace")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    config = json.loads((ROOT / "configs/boolean_smoke.json").read_text(encoding="utf-8"))
    upstream = args.upstream.resolve()
    commit = subprocess.check_output(["git", "-C", str(upstream), "rev-parse", "HEAD"], text=True).strip()
    if commit != config["upstream_commit"]:
        raise SystemExit(f"Expected upstream {config['upstream_commit']}, found {commit}.")

    sys.dont_write_bytecode = True
    sys.path.insert(0, str(upstream / "boolean"))
    from boolean_benchmark import BooleanBenchmarkRefined
    from modules.llm_interface import LLMInterface

    dataset = ROOT / config["dataset"]["path"]
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    benchmark = BooleanBenchmarkRefined(str(dataset))
    if (
        benchmark.variables != ["x", "y"]
        or benchmark.operators != set(config["dataset"]["expected_operators"])
        or benchmark.max_depth != config["dataset"]["max_depth"]
        or benchmark.mechanistic_opts != config["dataset"]["mechanistic_opts"]
    ):
        raise SystemExit("Dataset constraints differ from the D1 smoke configuration.")
    model = config["ollama"]["model"]
    options = config["ollama"]["options"]
    request_indices = count()

    with requests.Session() as session, (output / "requests.jsonl").open("x", encoding="utf-8") as log:
        tags = session.get(f"{OLLAMA}/api/tags", timeout=10)
        tags.raise_for_status()
        installed = next((entry for entry in tags.json()["models"] if entry["name"] == model), None)
        if installed is None:
            raise SystemExit(f"{model} must already be installed locally.")
        ollama_version = session.get(f"{OLLAMA}/api/version", timeout=10)
        ollama_version.raise_for_status()
        write_json(output / "metadata.json", {
            "config": config,
            "dataset_sha256": hashlib.sha256(dataset.read_bytes()).hexdigest(),
            "upstream_commit": commit,
            "python": platform.python_version(),
            "python_executable": sys.executable,
            "dependencies": {name: version(name) for name in DEPENDENCIES},
            "ollama_version": ollama_version.json()["version"],
            "model": installed,
            "request_seed_policy": "base seed plus zero-based HTTP request index",
        })

        class OllamaSmokeLLM(LLMInterface):
            def get_name(self):
                # The upstream checkpoint filename includes get_name() on Windows.
                return f"ollama-{model.replace(':', '-')}"

            def query(self, prompt):
                return self.query_with_usage(prompt)["response"]

            def query_with_usage(self, prompt):
                request_index = next(request_indices)
                request_options = {**options, "seed": options["seed"] + request_index}
                payload = {"model": model, "prompt": prompt, "stream": False, "options": request_options}
                entry = {"request_index": request_index, "request": payload}
                try:
                    response = session.post(f"{OLLAMA}/api/generate", json=payload, timeout=120)
                    entry["http_status"] = response.status_code
                    entry["raw_body"] = response.text
                    response.raise_for_status()
                    raw = response.json()
                    if not raw.get("done") or not isinstance(raw.get("response"), str):
                        raise ValueError("Incomplete Ollama response.")
                    text = raw["response"]
                    parsed = benchmark.parse_llm_response(text)
                    accepted = is_safe_boolean_expression(parsed)
                    entry.update(parsed_expression=parsed, accepted_by_syntax_guard=accepted)
                    prompt_tokens = raw.get("prompt_eval_count", 0)
                    completion_tokens = raw.get("eval_count", 0)
                    if not accepted:
                        text = ""
                except (requests.RequestException, ValueError) as error:
                    entry["error"] = str(error)
                    text = f"Error querying Ollama: {error}"
                    prompt_tokens = completion_tokens = 0
                finally:
                    log.write(json.dumps(entry, ensure_ascii=False) + "\n")
                    log.flush()
                return {
                    "response": text,
                    "usage": {
                        "prompt_tokens": prompt_tokens,
                        "completion_tokens": completion_tokens,
                        "total_tokens": prompt_tokens + completion_tokens,
                    },
                    "cost": 0.0,
                }

        results = benchmark.run_benchmark(
            OllamaSmokeLLM(),
            **config["benchmark"],
            checkpoint_dir=str(output / "checkpoints"),
            run_id="fixed-temperature",
        )
    write_json(output / "result.json", results)
    if (
        results["n_samples"] != config["benchmark"]["n_samples"]
        or results["error_summary"]["total_errors"]
        or not all(sample["n_recovered_gts"] for sample in results["per_sample_results"])
    ):
        raise SystemExit("Boolean smoke test failed. See saved results and request log.")


if __name__ == "__main__":
    main()
