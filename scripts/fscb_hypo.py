"""ACB-Hypo and FSCB-Hypo policies using only public Boolean feedback."""

from collections import deque
from dataclasses import dataclass
from math import exp, sqrt
from random import Random

import numpy as np

from scripts.candidate_history import CandidateHistory, HistoryStatus

TEMPERATURES = (0.2, 0.6, 1.0)
METHODS = ("ACB", "FSCB")
CAPACITIES = {"leaf": 2, "and_only": 1, "or_only": 1, "and_mixed": 3, "or_mixed": 3}
FRONTIER_FORMS = {
    "leaf": "a single variable",
    "and_only": "an AND-only form",
    "or_only": "an OR-only form",
    "and_mixed": "both gate types with AND at the root",
    "or_mixed": "both gate types with OR at the root",
}
ALPHA = 0.5
RIDGE = 1.0
WARMUP_FEEDBACK = 2
HISTORY_BYTES = 128
FRONTIER_BYTES = 128
BETA = 0.25
KAPPA = 1.0


def structural_bucket(key):
    operator, children = key
    if operator == "VAR":
        return "leaf"
    other = {"AND": "OR", "OR": "AND"}[operator]
    mixed = any(child[0] == other for child in children)
    return f"{operator.lower()}_{'mixed' if mixed else 'only'}"


def frontier_hint(bucket):
    return f"If consistent with all observations, consider {FRONTIER_FORMS[bucket]}. Any legal match is welcome."


@dataclass(frozen=True)
class Arm:
    mode: str
    temperature: float


class Bandit:
    def __init__(self, method, seed):
        if method not in METHODS:
            raise ValueError(f"Unknown method: {method}")
        dimensions = 5 if method == "ACB" else 6
        modes = ("standard",) if method == "ACB" else ("standard", "frontier")
        self.arms = tuple(Arm(mode, temperature) for mode in modes for temperature in TEMPERATURES)
        priority = list(self.arms)
        Random(seed).shuffle(priority)
        self.rank = {arm: i for i, arm in enumerate(priority)}
        self.matrices = {arm: RIDGE * np.eye(dimensions) for arm in self.arms}
        self.vectors = {arm: np.zeros(dimensions) for arm in self.arms}
        self.counts = dict.fromkeys(self.arms, 0)

    def choose(self, available, context):
        if min(self.counts[arm] for arm in available) < WARMUP_FEEDBACK:
            return min(available, key=lambda arm: (self.counts[arm], self.rank[arm]))

        def ucb(arm):
            matrix = self.matrices[arm]
            theta = np.linalg.solve(matrix, self.vectors[arm])
            uncertainty = sqrt(max(0., context @ np.linalg.solve(matrix, context)))
            return context @ theta + ALPHA * uncertainty, -self.rank[arm]

        return max(available, key=ucb)

    def update(self, arm, context, reward):
        self.matrices[arm] += np.outer(context, context)
        self.vectors[arm] += reward * context
        self.counts[arm] += 1


@dataclass(frozen=True)
class Decision:
    arm: Arm
    available: tuple[Arm, ...]
    context: tuple[float, ...]
    target: str | None
    target_coverage: float | None
    history: str


class Controller:
    def __init__(self, method, seed, *, beta=BETA):
        self.method = method
        self.bandit = Bandit(method, seed)
        self.beta = beta if method == "FSCB" else 0.

    def start_group(self, observations, *, seed):
        self.history = CandidateHistory(observations)
        self.n_observations = len(observations)
        self.recent = deque(maxlen=4)
        self.coverage = dict.fromkeys(CAPACITIES, 0)
        self.misses = dict.fromkeys(CAPACITIES, 0)
        self.bucket_order = list(CAPACITIES)
        Random(seed).shuffle(self.bucket_order)

    def decide(self, attempted, budget=4):
        valid = (HistoryStatus.VALID_NEW, HistoryStatus.VALID_DUPLICATE)
        n = len(self.recent) or 1
        context = (1., self.n_observations / 4, (budget - attempted) / budget,
                   sum(status in valid for status in self.recent) / n,
                   self.recent.count(HistoryStatus.VALID_NEW) / n)
        eligible = []
        if self.method == "FSCB":
            context += (sum(count > 0 for count in self.coverage.values()) / len(CAPACITIES),)
            if any(self.coverage.values()):
                eligible = [bucket for bucket in self.bucket_order
                            if self.coverage[bucket] < CAPACITIES[bucket] and self.misses[bucket] < 2
                            and len(frontier_hint(bucket).encode("utf-8")) <= FRONTIER_BYTES]
        target = min(eligible, key=lambda bucket: self.coverage[bucket] / CAPACITIES[bucket], default=None)
        available = tuple(arm for arm in self.bandit.arms if arm.mode == "standard" or target is not None)
        arm = self.bandit.choose(available, np.asarray(context))
        if arm.mode != "frontier":
            target = None
        ratio = self.coverage[target] / CAPACITIES[target] if target else None
        history = self.history.render(lambda text: len(text.encode("utf-8")), max_tokens=HISTORY_BYTES)
        return Decision(arm, available, context, target, ratio, history.structured.text)

    def observe(self, decision, response):
        status = self.history.observe(response)
        actual = reward = hit = None
        if status not in (HistoryStatus.INFRASTRUCTURE_FAILURE, HistoryStatus.MISSING_MECHANISTIC_KEY):
            if status == HistoryStatus.VALID_NEW:
                actual = structural_bucket(self.history.last_discovery_key)
                reward = 1. - self.beta + self.beta * exp(-self.coverage[actual] / KAPPA)
                self.coverage[actual] += 1
            else:
                reward = .2 if status == HistoryStatus.VALID_DUPLICATE else -.5
            if decision.target:
                hit = actual == decision.target
                self.misses[decision.target] = 0 if hit else self.misses[decision.target] + 1
            self.recent.append(status)
            self.bandit.update(decision.arm, np.asarray(decision.context), reward)
        return {"status": status.value, "reward": reward, "actual_bucket": actual, "frontier_hit": hit}
