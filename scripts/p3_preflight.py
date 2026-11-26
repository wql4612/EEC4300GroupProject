"""Validate P3's frozen inputs and inspect the launch environment without HTTP."""

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from importlib.metadata import version as distribution_version
from pathlib import Path

from scripts import run_d2_prompt as collector
from scripts.ollama_process import read_service_environment

ROOT = Path(__file__).resolve().parents[1]
UPSTREAM = ROOT.parent / "HypoSpace"
DATASET = ROOT / "data/d1/boolean_basic_depth2.json"
D2_SPLIT = ROOT / "results/d2/prompt-20261126/split_manifest.json"
UPSTREAM_COMMIT = "c69e9318577b34b5b896996571aefd4ba6053f58"
DATASET_SHA256 = "84ad5ed3385303bfd0cdc5507fcba890bd06921c2594908ca9b839aebd389bab"
D2_SPLIT_SHA256 = "94f7b0b1ea8d17b94b2a87d6a1c177597fd3134792b261e96094562dd35f981a"
OLLAMA_VERSION = "0.40.14"
MODEL_NAME = "qwen2.5:3b"
TEMPLATE_SHA256 = "eb4402837c7829a690fa845de4d7f3fd842c2adee476d5341da8a46ea9255175"
SCHEMA_VERSION = 2
PLANNED_REQUESTS = 180
MIN_FREE_VRAM_MIB = 4096
MIN_FREE_DISK_BYTES = 6 * 1024 * 1024
PROMPT_FILES = {"P1": "P1.txt", "P2": "P2.txt", "P3": "P3-draft.txt"}
RESERVED_GROUPS = (
    "n2_inputs_01_10_outputs_11", "n2_inputs_00_01_outputs_01",
    "n3_inputs_00_01_10_outputs_010", "n3_inputs_01_10_11_outputs_011",
)
SELECTION_FILE = "selection_manifest.json"
FROZEN_HASH_FIELDS = {
    "online_manifest_sha256", "selection_manifest_sha256", "dataset_sha256",
    "collector_sha256", "verifier_sha256",
}
LOCAL_SOURCE_FILES = (
    "scripts/score_p3_prompt.py", "scripts/score_d2_prompt.py", "scripts/prepare_d2_prompt.py",
    "scripts/run_boolean_smoke.py", "scripts/ollama_process.py", "scripts/p3_collection_seal.py",
)
SCORING_DISTRIBUTIONS = ("numpy", "scipy", "sympy", "mpmath", "PyYAML")
COMMON_FIELDS = {
    "schema_version", "upstream_commit", "ollama_version", "prompt_hashes",
    "planned_requests", "rollout_seeds", "local_source_hashes", "scoring_versions",
} | FROZEN_HASH_FIELDS
FREEZE_FIELDS = COMMON_FIELDS | {"model", "options"}
PREFLIGHT_FIELDS = COMMON_FIELDS | {"freeze_sha256", "model_digest", "environment", "checks"}
MODEL_FIELDS = {"name", "digest", "template_sha256", "parameters"}
ENVIRONMENT_FIELDS = {"python_version", "gpu", "log_directory", "ollama"}
RUNTIME_FIELDS = {"version", "executable", "model_directory", "listener_pid", "listen_address"}
GPU_FIELDS = {"name", "total_mib", "free_mib"}
LOG_FIELDS = {"writable", "free_bytes"}
REQUIRED_CHECKS = (
    "registered_public_plan", "frozen_file_hashes", "pinned_clean_upstream", "python_3_11",
    "ollama_cli_registry_process_loopback", "all_model_blob_hashes_template_and_parameters", "nvidia_gpu_and_vram",
    "writable_log_directory_and_free_space",
)
OUTPUT_NAMES = ("requests.jsonl", "collection_seal.json", ".collection_seal.incomplete",
                "official-results.json", "group-seed-metrics.csv")


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def digest_file(path):
    with path.open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


def load_object(data, filename):
    def unique_fields(pairs):
        fields = {}
        for name, value in pairs:
            if name in fields:
                raise RuntimeError(f"{filename} contains duplicate field {name!r}")
            fields[name] = value
        return fields

    try:
        return json.loads(data, object_pairs_hook=unique_fields)
    except (ValueError, UnicodeError) as error:
        raise RuntimeError(f"{filename} is not valid JSON") from error


def require_fields(value, required, label):
    if not isinstance(value, dict):
        raise RuntimeError(f"{label} must be a JSON object")
    missing = sorted(required - value.keys())
    unknown = sorted(value.keys() - required)
    if missing or unknown:
        raise RuntimeError(f"{label} fields: missing={missing}, unknown={unknown}")


def require_sha256(value, label):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", value):
        raise RuntimeError(f"{label} must be a 64-digit SHA-256")


def fingerprint(group_id):
    text = collector.public_observation_text(group_id)
    rows = [tuple(map(int, re.findall(r"[01]", line))) for line in text.splitlines()]
    return collector.sha256(json.dumps(rows).encode("utf-8"))


def registered_selection():
    return {
        "schema_version": SCHEMA_VERSION,
        "d2_split_sha256": D2_SPLIT_SHA256,
        "selected": {
            f"H{index}": {"group_id": group_id, "observation_fingerprint": fingerprint(group_id)}
            for index, group_id in enumerate(collector.P3_GROUPS, 1)
        },
        "reserved": {group_id: fingerprint(group_id) for group_id in RESERVED_GROUPS},
    }


def registered_model():
    return {
        "name": MODEL_NAME, "digest": collector.MODEL_DIGEST,
        "template_sha256": TEMPLATE_SHA256, "parameters": None,
    }


def validate_common(receipt, label):
    for field in FROZEN_HASH_FIELDS:
        require_sha256(receipt[field], f"{label}.{field}")
    hashes = receipt["local_source_hashes"]
    require_fields(hashes, set(LOCAL_SOURCE_FILES), f"{label}.local_source_hashes")
    for name, digest in hashes.items():
        require_sha256(digest, f"{label}.local_source_hashes.{name}")
    versions = receipt["scoring_versions"]
    require_fields(versions, {"python", *SCORING_DISTRIBUTIONS}, f"{label}.scoring_versions")
    if any(not isinstance(value, str) or not value for value in versions.values()):
        raise RuntimeError(f"{label}.scoring_versions must contain nonempty version strings")
    if type(receipt["schema_version"]) is not int or receipt["schema_version"] != SCHEMA_VERSION:
        raise RuntimeError(f"{label}.schema_version differs from the supported version")
    if type(receipt["planned_requests"]) is not int or receipt["planned_requests"] != PLANNED_REQUESTS:
        raise RuntimeError(f"{label}.planned_requests must be 180")
    seeds = receipt["rollout_seeds"]
    if not isinstance(seeds, list) or any(type(seed) is not int for seed in seeds) or seeds != list(collector.SEEDS):
        raise RuntimeError(f"{label}.rollout_seeds differs from the registered seeds")
    if receipt["upstream_commit"] != UPSTREAM_COMMIT:
        raise RuntimeError(f"{label}.upstream_commit differs from the registered HypoSpace commit")
    if receipt["ollama_version"] != OLLAMA_VERSION:
        raise RuntimeError(f"{label}.ollama_version must be {OLLAMA_VERSION}")
    hashes = receipt["prompt_hashes"]
    require_fields(hashes, set(collector.P3_VARIANTS), f"{label}.prompt_hashes")
    for condition, digest in hashes.items():
        require_sha256(digest, f"{label}.prompt_hashes.{condition}")
    if hashes != collector.P3_PROMPT_HASHES:
        raise RuntimeError(f"{label}.prompt_hashes differs from the registered Prompt hashes")
    if receipt["dataset_sha256"] != DATASET_SHA256:
        raise RuntimeError(f"{label}.dataset_sha256 differs from the registered dataset")
    if receipt["selection_manifest_sha256"] != collector.sha256(json_bytes(registered_selection())):
        raise RuntimeError(f"{label}.selection_manifest_sha256 differs from the registered selection")


def validate_freeze(freeze):
    require_fields(freeze, FREEZE_FIELDS, "Freeze")
    validate_common(freeze, "Freeze")
    model = freeze["model"]
    require_fields(model, MODEL_FIELDS, "Freeze.model")
    for field in ("digest", "template_sha256"):
        require_sha256(model[field], f"Freeze.model.{field}")
    if model != registered_model():
        raise RuntimeError("Freeze.model differs from the registered name, digest, template or parameters")
    options = freeze["options"]
    require_fields(options, set(collector.OPTIONS), "Freeze.options")
    if any(type(options[key]) is not type(value) for key, value in collector.OPTIONS.items()) or options != collector.OPTIONS:
        raise RuntimeError("Freeze.options differs from the registered generation parameters")


def validate_environment(environment):
    require_fields(environment, ENVIRONMENT_FIELDS, "Preflight.environment")
    version = environment["python_version"]
    if not isinstance(version, str) or not re.fullmatch(r"3\.11\.\d+", version):
        raise RuntimeError("The experiment requires Python 3.11")
    runtime = environment["ollama"]
    require_fields(runtime, RUNTIME_FIELDS, "Preflight.environment.ollama")
    if runtime["version"] != OLLAMA_VERSION or runtime["listen_address"] != "127.0.0.1:11434":
        raise RuntimeError("Unexpected Ollama version or listen address")
    if type(runtime["listener_pid"]) is not int or runtime["listener_pid"] <= 0:
        raise RuntimeError("Ollama listener PID is invalid")
    for field in ("executable", "model_directory"):
        if not isinstance(runtime[field], str) or not Path(runtime[field]).is_absolute():
            raise RuntimeError(f"Ollama {field} must be an absolute path")
    gpu = environment["gpu"]
    require_fields(gpu, GPU_FIELDS, "Preflight.environment.gpu")
    if not isinstance(gpu["name"], str) or not re.fullmatch(r"NVIDIA GeForce RTX 4060(?: Laptop GPU)?", gpu["name"]):
        raise RuntimeError("The experiment requires an NVIDIA GeForce RTX 4060")
    if type(gpu["total_mib"]) is not int or not 8000 <= gpu["total_mib"] <= 8192:
        raise RuntimeError("The experiment requires the registered 8 GiB GPU capacity")
    if type(gpu["free_mib"]) is not int or not MIN_FREE_VRAM_MIB <= gpu["free_mib"] <= gpu["total_mib"]:
        raise RuntimeError(f"GPU must have at least {MIN_FREE_VRAM_MIB} MiB of free VRAM")
    log = environment["log_directory"]
    require_fields(log, LOG_FIELDS, "Preflight.environment.log_directory")
    if log["writable"] is not True:
        raise RuntimeError("Log directory is not writable")
    if type(log["free_bytes"]) is not int or log["free_bytes"] < MIN_FREE_DISK_BYTES:
        raise RuntimeError(f"Log directory requires at least {MIN_FREE_DISK_BYTES} free bytes")


def validate_preflight(preflight, freeze, freeze_bytes):
    require_fields(preflight, PREFLIGHT_FIELDS, "Preflight")
    validate_common(preflight, "Preflight")
    require_sha256(preflight["freeze_sha256"], "Preflight.freeze_sha256")
    require_sha256(preflight["model_digest"], "Preflight.model_digest")
    for field in sorted(COMMON_FIELDS):
        if preflight[field] != freeze[field]:
            raise RuntimeError(f"Preflight.{field} does not match Freeze")
    if preflight["freeze_sha256"] != collector.sha256(freeze_bytes):
        raise RuntimeError("Preflight.freeze_sha256 does not match freeze.json")
    if preflight["model_digest"] != freeze["model"]["digest"]:
        raise RuntimeError("Preflight.model_digest does not match the registered model")
    checks = preflight["checks"]
    require_fields(checks, set(REQUIRED_CHECKS), "Preflight.checks")
    for check in REQUIRED_CHECKS:
        if checks[check] is not True:
            raise RuntimeError(f"Preflight check did not pass: {check}")
    validate_environment(preflight["environment"])


def source_hashes():
    return {
        "collector_sha256": digest_file(Path(collector.__file__)),
        "verifier_sha256": digest_file(Path(__file__)),
        "local_source_hashes": {name: digest_file(ROOT / name) for name in LOCAL_SOURCE_FILES},
    }


def scoring_versions():
    return {
        "python": ".".join(map(str, sys.version_info[:3])),
        **{name: distribution_version(name) for name in SCORING_DISTRIBUTIONS},
    }


def verify_upstream():
    git = ("git", "-C", str(UPSTREAM))
    commit = local_command(*git, "rev-parse", "HEAD").strip()
    if commit != UPSTREAM_COMMIT:
        raise RuntimeError(f"HypoSpace HEAD is {commit}, expected {UPSTREAM_COMMIT}")
    if local_command(*git, "status", "--porcelain", "--untracked-files=all").strip():
        raise RuntimeError("The pinned HypoSpace checkout must be clean")


def local_command(*args, env=None):
    return subprocess.run(
        args, check=True, capture_output=True, text=True, encoding="utf-8", timeout=20, env=env,
    ).stdout


def offline_file_hashes(run_dir, *, isolated):
    paths = (DATASET, run_dir / SELECTION_FILE)
    if isolated:
        # The collector receives only two digests. This separate process has no
        # model interface, parses no dataset fields and emits no file content.
        hashes = local_command(
            sys.executable, "-I", "-S", "-B", "-c",
            "import hashlib,sys\nfor p in sys.argv[1:]:\n"
            " with open(p,'rb') as f: print(hashlib.file_digest(f,'sha256').hexdigest())",
            *(str(path) for path in paths),
        ).splitlines()
    else:
        hashes = [digest_file(path) for path in paths]
    if len(hashes) != 2:
        raise RuntimeError("Offline file hash verifier did not return two digests")
    return dict(zip(("dataset_sha256", "selection_manifest_sha256"), hashes, strict=True))


def read_bundle(run_dir, *, online):
    """Validate a sealed bundle before collection or replay."""
    config_bytes = (run_dir / "online_manifest.json").read_bytes()
    freeze_bytes = (run_dir / "freeze.json").read_bytes()
    manifest = load_object(config_bytes, "online_manifest.json")
    freeze = load_object(freeze_bytes, "freeze.json")
    validate_freeze(freeze)
    collector.validate_public_manifest(manifest, freeze, collector.P3_VARIANTS)
    preflight_bytes = (run_dir / "preflight.json").read_bytes()
    preflight = load_object(preflight_bytes, "preflight.json")
    validate_preflight(preflight, freeze, freeze_bytes)
    actual_hashes = {"online_manifest_sha256": collector.sha256(config_bytes), **source_hashes()}
    for field, digest in actual_hashes.items():
        if digest != freeze[field]:
            raise RuntimeError(f"Actual {field} does not match Freeze")
    if scoring_versions() != freeze["scoring_versions"]:
        raise RuntimeError("Current scoring dependency versions differ from Freeze")
    verify_upstream()
    for field, digest in offline_file_hashes(run_dir, isolated=online).items():
        if digest != freeze[field]:
            raise RuntimeError(f"Actual {field} does not match Freeze")
    if online:
        current = probe_environment(run_dir)
        validate_environment(current)
        recorded = preflight["environment"]
        if current["python_version"] != recorded["python_version"] or any(
            current["gpu"][field] != recorded["gpu"][field] for field in ("name", "total_mib")
        ):
            raise RuntimeError("Current Python or GPU identity differs from Preflight")
        if any(current["ollama"][key] != recorded["ollama"][key] for key in RUNTIME_FIELDS - {"listener_pid"}):
            raise RuntimeError("Current Ollama installation or model path differs from Preflight")
    return manifest, freeze, {
        "online_manifest_sha256": freeze["online_manifest_sha256"],
        "freeze_sha256": collector.sha256(freeze_bytes),
        "preflight_sha256": collector.sha256(preflight_bytes),
    }


def windows_ollama_installation():
    import winreg

    executable = shutil.which("ollama")
    if not executable:
        raise RuntimeError("Ollama executable not found on PATH")
    executable = Path(executable).resolve()
    versions = set()
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
            try:
                uninstall = winreg.OpenKey(hive, r"Software\Microsoft\Windows\CurrentVersion\Uninstall",
                                          access=winreg.KEY_READ | view)
            except FileNotFoundError:
                continue
            with uninstall:
                for index in range(winreg.QueryInfoKey(uninstall)[0]):
                    name = winreg.EnumKey(uninstall, index)
                    with winreg.OpenKey(uninstall, name) as entry:
                        try:
                            display = winreg.QueryValueEx(entry, "DisplayName")[0]
                            install = winreg.QueryValueEx(entry, "InstallLocation")[0]
                            version = winreg.QueryValueEx(entry, "DisplayVersion")[0]
                        except FileNotFoundError:
                            continue
                        if display.startswith("Ollama ") and install and Path(install).resolve() == executable.parent:
                            versions.add(version)
    if len(versions) != 1:
        raise RuntimeError("Could not establish the installed Ollama version for the executable on PATH")
    model_dir = None
    for hive, key in ((winreg.HKEY_CURRENT_USER, "Environment"), (
        winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment",
    )):
        try:
            with winreg.OpenKey(hive, key) as environment:
                model_dir = winreg.ExpandEnvironmentStrings(winreg.QueryValueEx(environment, "OLLAMA_MODELS")[0])
        except FileNotFoundError:
            continue
        if model_dir:
            break
    root = Path(model_dir) if model_dir else Path.home() / ".ollama/models"
    if not root.is_absolute():
        raise RuntimeError("The registry must specify an absolute Ollama model directory")
    caller_models = os.environ.get("OLLAMA_MODELS")
    if caller_models and Path(caller_models).resolve() != root.resolve():
        raise RuntimeError("This process's model directory differs from the Windows registry")
    return executable, versions.pop(), root


def verify_ollama_listener(executable):
    listeners = []
    for line in local_command("netstat.exe", "-ano").splitlines():
        columns = line.split()
        if (
            len(columns) == 5 and columns[0] == "TCP" and columns[2] in {"0.0.0.0:0", "[::]:0"}
        ):
            listeners.append((columns[1], int(columns[4])))
    endpoints = [(address, pid) for address, pid in listeners if address.endswith(":11434")]
    if len(endpoints) != 1 or endpoints[0][0] != "127.0.0.1:11434":
        raise RuntimeError("Ollama must listen exclusively on 127.0.0.1:11434")
    _, pid = endpoints[0]
    if any(owner == pid and not address.startswith(("127.0.0.1:", "[::1]:")) for address, owner in listeners):
        raise RuntimeError("Ollama owns a non-loopback listener")
    powershell = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    command = f"$ErrorActionPreference='Stop'; (Get-Process -Id {pid}).Path"
    path = local_command(str(powershell), "-NoProfile", "-NonInteractive", "-Command", command).strip()
    if not path or Path(path).resolve() != executable:
        raise RuntimeError("The loopback Ollama listener does not belong to the registered installation")
    return pid


def inspect_ollama_cli_version(executable):
    # Ollama's version command includes its embedded client version if the server
    # cannot be contacted. Port zero cannot connect to the live API, and all proxies are disabled.
    env = {key: value for key, value in os.environ.items() if not key.upper().endswith("_PROXY")}
    env.update(OLLAMA_HOST="http://127.0.0.1:0", NO_PROXY="*")
    output = local_command(str(executable), "--version", env=env)
    match = re.search(r"(?m)^Warning: client version is ([0-9]+\.[0-9]+\.[0-9]+)\s*$", output)
    if not match or "could not connect to a running Ollama instance" not in output:
        raise RuntimeError("Unable to verify the offline Ollama CLI version")
    return match[1]


def verify_ollama_runtime(executable, version, models):
    cli_version = inspect_ollama_cli_version(executable)
    if cli_version != version:
        raise RuntimeError("The Ollama CLI version differs from the Windows registry")
    pid = verify_ollama_listener(executable)
    environment = read_service_environment(pid)
    configured_models = environment.get("OLLAMA_MODELS")
    if not configured_models or not Path(configured_models).is_absolute() or Path(configured_models).resolve() != models.resolve():
        raise RuntimeError("The live Ollama process model directory differs from the Windows registry")
    if environment.get("OLLAMA_HOST") not in {"127.0.0.1:11434", "http://127.0.0.1:11434"}:
        raise RuntimeError("The live Ollama process is not configured for IPv4 loopback only")
    return {"version": version, "executable": str(executable), "model_directory": str(models.resolve()),
            "listener_pid": pid, "listen_address": "127.0.0.1:11434"}


def verify_local_model(root):
    manifest = root / "manifests/registry.ollama.ai/library/qwen2.5/3b"
    data = manifest.read_bytes()
    if collector.sha256(data) != collector.MODEL_DIGEST:
        raise RuntimeError("Local model digest differs from the registered Qwen2.5-3B digest")
    metadata = load_object(data, "Ollama model manifest")
    template = None
    verified = set()
    blobs = root / "blobs"
    for layer in [metadata["config"], *metadata["layers"]]:
        digest = layer["digest"].removeprefix("sha256:")
        require_sha256(digest, "Ollama layer digest")
        blob = blobs / f"sha256-{digest}"
        if blob.stat().st_size != layer["size"]:
            raise RuntimeError(f"Ollama model blob size differs: {layer['mediaType']}")
        content_type = layer["mediaType"]
        if content_type == "application/vnd.ollama.image.params":
            raise RuntimeError("Ollama model contains non-default sampling parameters")
        if digest_file(blob) != digest:
            raise RuntimeError(f"Ollama model blob hash differs: {content_type}")
        verified.add(blob.name)
        if content_type == "application/vnd.ollama.image.template":
            template = digest
    if template != TEMPLATE_SHA256:
        raise RuntimeError("Ollama model template differs from the registered template")
    for blob in sorted(blobs.iterdir()):
        name = blob.name.lower()
        if name not in verified and re.fullmatch(r"sha256-[0-9a-f]{64}", name):
            if digest_file(blob) != name.removeprefix("sha256-"):
                raise RuntimeError(f"Ollama model blob hash differs: {blob.name}")


def probe_environment(run_dir):
    if sys.platform != "win32":
        raise RuntimeError("P3 launch metadata checks require the registered Windows environment")
    executable, version, models = windows_ollama_installation()
    if version != OLLAMA_VERSION:
        raise RuntimeError(f"Installed Ollama version is {version}, expected {OLLAMA_VERSION}")
    runtime = verify_ollama_runtime(executable, version, models)
    verify_local_model(models)
    if verify_ollama_listener(executable) != runtime["listener_pid"]:
        raise RuntimeError("Ollama listener changed during model verification")
    readings = local_command(
        "nvidia-smi", "--query-gpu=name,memory.total,memory.free", "--format=csv,noheader,nounits",
    ).splitlines()
    if len(readings) != 1:
        raise RuntimeError("Expected exactly one NVIDIA GPU in the registered environment")
    name, total, free = (part.strip() for part in readings[0].split(","))
    with tempfile.NamedTemporaryFile(dir=run_dir, prefix=".p3-write-probe-") as probe:
        probe.write(b"P3")
        probe.flush()
    environment = {
        "python_version": ".".join(map(str, sys.version_info[:3])),
        "ollama": runtime,
        "gpu": {"name": name, "total_mib": int(total), "free_mib": int(free)},
        "log_directory": {"writable": True, "free_bytes": shutil.disk_usage(run_dir).free},
    }
    validate_environment(environment)
    return environment
