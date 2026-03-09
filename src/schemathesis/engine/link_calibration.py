from __future__ import annotations

from dataclasses import dataclass, field

MIN_SAMPLES = 5
# Optimistic prior — links start fully trusted until we have evidence otherwise.
DEFAULT_USE_PROBABILITY = 0.85
# Floor — always allow minimal exploration even for bad links.
MIN_PROBABILITY = 0.05


@dataclass(slots=True)
class TransitionScore:
    successes: int = 0
    failures: int = 0

    @property
    def use_probability(self) -> float:
        total = self.successes + self.failures
        if total < MIN_SAMPLES:
            return DEFAULT_USE_PROBABILITY
        return max(MIN_PROBABILITY, self.successes / total)

    def merge(self, other: TransitionScore) -> None:
        self.successes += other.successes
        self.failures += other.failures


@dataclass(slots=True)
class LinkCalibrationState:
    """Double-buffered per-link score store.

    `read` is the stable snapshot a single Hypothesis run consults; `write` accumulates
    outcomes during the run and is merged into `read` at iteration boundaries via
    `begin_iteration()`. The split keeps probabilities stable within one replay so
    Hypothesis's data tree remains coherent.
    """

    read: dict[str, TransitionScore] = field(default_factory=dict)
    write: dict[str, TransitionScore] = field(default_factory=dict)

    def begin_iteration(self) -> None:
        for transition_id, score in self.write.items():
            self.read.setdefault(transition_id, TransitionScore()).merge(score)
        self.write.clear()

    def record(self, transition_id: str, *, success: bool) -> None:
        entry = self.write.setdefault(transition_id, TransitionScore())
        if success:
            entry.successes += 1
        else:
            entry.failures += 1

    def use_probability(self, transition_id: str) -> float:
        return self.read.get(transition_id, TransitionScore()).use_probability

    def is_calibrated(self, transition_id: str) -> bool:
        score = self.read.get(transition_id)
        return score is not None and score.successes + score.failures >= MIN_SAMPLES
