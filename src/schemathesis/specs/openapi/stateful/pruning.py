from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from schemathesis.core.transport import Response
    from schemathesis.engine.recorder import ScenarioRecorder
    from schemathesis.generation.stateful.state_machine import StepInput

# Minimum observations before pruning kicks in (below this: optimistic default)
MIN_SAMPLES = 5
# Initial probability of using a link-extracted value (optimistic prior)
DEFAULT_USE_PROBABILITY = 0.85
# Floor probability — always allow minimal exploration even for bad links
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
class PruningState:
    read: dict[str, TransitionScore] = field(default_factory=dict)
    write: dict[str, TransitionScore] = field(default_factory=dict)
    enabled: bool = True

    def begin_iteration(self) -> None:
        """Merge write into read, clear write. Call before each Hypothesis suite run."""
        for transition_id, score in self.write.items():
            self.read.setdefault(transition_id, TransitionScore()).merge(score)
        self.write.clear()

    def record(self, transition_id: str, *, success: bool) -> None:
        if not self.enabled:
            return
        entry = self.write.setdefault(transition_id, TransitionScore())
        if success:
            entry.successes += 1
        else:
            entry.failures += 1

    def use_probability(self, transition_id: str) -> float:
        return self.read.get(transition_id, TransitionScore()).use_probability

    def is_calibrated(self, transition_id: str) -> bool:
        """Return True if enough observations exist to trust the learned probability."""
        if not self.enabled:
            return False
        score = self.read.get(transition_id)
        return score is not None and score.successes + score.failures >= MIN_SAMPLES


def record_pruning_observation(
    pruning: PruningState,
    response: Response,
    step_input: StepInput,
    recorder: ScenarioRecorder,
) -> None:
    """Record a pruning observation if the outcome is attributable to the link.

    4xx responses (excluding 401/403) are treated as failures: they indicate the
    link-extracted values were rejected by the server. 401/403 are skipped because
    they reflect auth configuration issues, not link quality. 5xx are skipped because
    server errors mask whether the request itself was valid.

    When a recorder is provided, 4xx responses are additionally filtered: if a prior
    successful DELETE in the same scenario explains the failure, the observation is
    skipped to avoid penalising a link that was actually correct.
    """
    if (
        step_input.transition is None
        or not step_input.is_applied
        or response.status_code in {401, 403}
        or response.status_code >= 500
    ):
        return
    if step_input.case.meta is not None and step_input.case.meta.generation.mode.is_negative:
        return
    if response.status_code >= 400:
        from schemathesis.specs.openapi.checks import resource_was_deleted

        if resource_was_deleted(recorder, step_input.case):
            return
    success = 200 <= response.status_code < 400
    pruning.record(step_input.transition.id, success=success)
