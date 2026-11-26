"""Offline reference for comparing two Boolean basic candidate history policies.

Create one instance per trajectory and feed it completed responses in slot order.
Pass None for a slot that failed in the infrastructure. The reference has no
model client, labeled dataset, reward, or dependency on historical run files.
"""

from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
import sys

from scripts.run_boolean_smoke import is_safe_boolean_expression

UPSTREAM_BOOLEAN = Path(__file__).resolve().parents[2] / "HypoSpace" / "boolean"
sys.path.insert(0, str(UPSTREAM_BOOLEAN))
from boolean_benchmark import BooleanBenchmarkRefined
from boolean_dataset import BooleanObservation


class HistoryStatus(StrEnum):
    INFRASTRUCTURE_FAILURE = "INFRASTRUCTURE_FAILURE"
    UNPARSEABLE = "UNPARSEABLE"
    OUT_OF_SPACE = "OUT_OF_SPACE"
    OBSERVATION_MISMATCH = "OBSERVATION_MISMATCH"
    MISSING_MECHANISTIC_KEY = "MISSING_MECHANISTIC_KEY"
    VALID_DUPLICATE = "VALID_DUPLICATE"
    VALID_NEW = "VALID_NEW"


@dataclass(frozen=True)
class HistoryBlock:
    text: str
    token_count: int


@dataclass(frozen=True)
class HistoryComparison:
    original: HistoryBlock
    structured: HistoryBlock


class CandidateHistory:
    def __init__(self, observations: Iterable[BooleanObservation]):
        public_rows = []
        for row in observations:
            if (
                type(row) is not BooleanObservation
                or set(row.inputs) != {"x", "y"}
                or any(type(value) is not int or value not in (0, 1)
                       for value in (*row.inputs.values(), row.output))
            ):
                raise ValueError("Expected public Boolean observations with binary x, y and output")
            public_rows.append(BooleanObservation(dict(row.inputs), row.output))
        if not public_rows:
            raise ValueError("Expected at least one public Boolean observation")

        # Pin the public D1 Grammar and key options. The upstream constructor
        # loads a labeled dataset, so initialize only the fields its methods use.
        self._benchmark = BooleanBenchmarkRefined.__new__(BooleanBenchmarkRefined)
        self._benchmark.variables = ["x", "y"]
        self._benchmark.operators = {"AND", "OR"}
        self._benchmark.max_depth = 2
        self._benchmark.mechanistic_opts = {
            "apply_commutativity": True,
            "apply_idempotence_and_or": True,
            "flatten_associativity": True,
        }
        self._observations = public_rows
        self._original = []
        self._recent = deque(maxlen=4)
        self._seen_keys = set()
        self._latest_failure = None
        self.last_discovery_key = None

    def observe(self, response: str | None) -> HistoryStatus:
        """Apply the official Parser and public Validator to one archived slot."""
        self.last_discovery_key = None
        if response is None:
            return HistoryStatus.INFRASTRUCTURE_FAILURE
        parsed = self._benchmark.parse_llm_response(response)
        if not parsed:
            self._latest_failure = HistoryStatus.UNPARSEABLE
            return self._latest_failure

        self._original.append(parsed)
        # Upstream calls SymPy on strings. Keep its existing local guard ahead
        # of all AST operations. This is history policy, separate from scoring.
        if not is_safe_boolean_expression(parsed) or not self._benchmark._in_space(parsed):
            self._latest_failure = HistoryStatus.OUT_OF_SPACE
            return self._latest_failure
        consistent, _ = self._benchmark.validate_expression(parsed, self._observations)
        if not consistent:
            self._latest_failure = HistoryStatus.OBSERVATION_MISMATCH
            return self._latest_failure
        key = self._benchmark.get_expression_mechanistic_key(parsed)
        if key is None:
            self._latest_failure = HistoryStatus.MISSING_MECHANISTIC_KEY
            return self._latest_failure
        if key in self._seen_keys:
            return HistoryStatus.VALID_DUPLICATE
        self._seen_keys.add(key)
        self._recent.append(parsed)
        self.last_discovery_key = key
        return HistoryStatus.VALID_NEW

    def render(
        self, count_tokens: Callable[[str], int], *, max_tokens: int = 128,
        include_diagnostic: bool = True,
    ) -> HistoryComparison:
        """Use the same slot budget for both views, measured on each whole block.

        Keep full entries in chronological order and drop older entries first.
        If the most recent entry cannot fit with the optional diagnostic, try
        the candidates alone. Budgeting does not mutate trajectory memory.
        """
        def measure(text):
            count = count_tokens(text)
            if type(count) is not int or count < 0:
                raise ValueError("Token counter must return a nonnegative integer")
            return HistoryBlock(text, count)

        empty = measure("None")
        if type(max_tokens) is not int or max_tokens < empty.token_count:
            raise ValueError("Token budget cannot fit the empty history marker")

        def fit(expressions, label=None):
            lines = [f"Expression: {value}" for value in expressions]
            suffix = f"\nDiagnostic: {label}" if label is not None else ""
            for tail in (suffix, "") if suffix else ("",):
                for start in range(len(lines)):
                    block = measure("\n".join(lines[start:]) + tail)
                    if block.token_count <= max_tokens:
                        return block
            if suffix:
                block = measure(empty.text + suffix)
                if block.token_count <= max_tokens:
                    return block
            return empty

        label = self._latest_failure if include_diagnostic else None
        return HistoryComparison(fit(self._original), fit(self._recent, label))
