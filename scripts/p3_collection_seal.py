"""Seal and verify the complete byte-for-byte P3 request journal."""

import re
from datetime import datetime, timezone

from scripts import p3_preflight as preflight
from scripts.run_d2_prompt import sha256

SEAL = "collection_seal.json"
PENDING_SEAL = ".collection_seal.incomplete"
SCHEMA_VERSION = 1
DOCUMENT_HASHES = {"freeze_sha256", "preflight_sha256", "online_manifest_sha256"}
INTEGER_FIELDS = {"schema_version", "requests_bytes", "requests_record_count", "actual_requests", "planned_requests"}
FIELDS = DOCUMENT_HASHES | INTEGER_FIELDS | {"requests_sha256", "completion_timestamp"}
UTC_TIMESTAMP = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?(?:Z|\+00:00)")


def journal_counts(data):
    rows = data.split(b"\n")
    record_count = len(rows) - 1
    planned = preflight.PLANNED_REQUESTS
    if (
        rows[-1] or not all(rows[:-1]) or not planned <= record_count <= 2 * planned
        or (record_count - planned) % 12
    ):
        raise RuntimeError("Collection journal is incomplete or does not contain whole paired blocks")
    return {
        "requests_bytes": len(data), "requests_record_count": record_count,
        "actual_requests": record_count, "planned_requests": planned,
    }


def make_receipt(data, bindings):
    """Build the receipt after the collector has synced the complete journal."""
    counts = journal_counts(data)
    return {
        **bindings, **counts, "schema_version": SCHEMA_VERSION, "requests_sha256": sha256(data),
        "completion_timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }


def verify_collection(run_dir, bindings):
    """Return the exact verified bytes used by the scorer, without reopening the journal."""
    path = run_dir / SEAL
    if not path.is_file():
        raise RuntimeError(f"{SEAL} is required before P3 scoring")
    seal = preflight.load_object(path.read_bytes(), SEAL)
    preflight.require_fields(seal, FIELDS, SEAL)
    for key in DOCUMENT_HASHES | {"requests_sha256"}:
        preflight.require_sha256(seal[key], f"{SEAL}.{key}")
    for key in INTEGER_FIELDS:
        if type(seal[key]) is not int:
            raise RuntimeError(f"{SEAL}.{key} must be an integer")
    timestamp = seal["completion_timestamp"]
    if not isinstance(timestamp, str) or not UTC_TIMESTAMP.fullmatch(timestamp):
        raise RuntimeError(f"{SEAL}.completion_timestamp must be an ISO 8601 UTC timestamp")
    try:
        datetime.fromisoformat(timestamp)
    except ValueError as error:
        raise RuntimeError(f"{SEAL}.completion_timestamp is not a valid UTC date and time") from error
    for key, expected in bindings.items():
        if seal[key] != expected:
            raise RuntimeError(f"{SEAL}.{key} differs from the validated bundle")
    data = (run_dir / "requests.jsonl").read_bytes()
    if seal["requests_sha256"] != sha256(data):
        raise RuntimeError("Request journal SHA-256 differs from collection_seal.json")
    counts = journal_counts(data)
    if seal["requests_record_count"] != counts["requests_record_count"]:
        raise RuntimeError(f"{SEAL}.requests_record_count differs from the archived JSONL record count")
    if seal["schema_version"] != SCHEMA_VERSION or any(seal[key] != value for key, value in counts.items()):
        raise RuntimeError("Collection seal counts or schema version differ from the complete request journal")
    return data, seal
