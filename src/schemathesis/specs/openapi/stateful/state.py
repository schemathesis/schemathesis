from __future__ import annotations

from dataclasses import dataclass

# When there is not enough data, be optimistic
INITIAL_SUCCESS_RATE = 0.7
BASE_EXPLORATION_RATE = 0.15
MEDIUM_CONFIDENCE_THRESHOLD = 50
HIGH_CONFIDENCE_THRESHOLD = 100
CONSISTENT_FAILURE_PENALTY = 0.03
MAX_BOOST = 0.25


@dataclass
class TransitionState:
    parameters: dict[str, ParameterState]
    successes: int
    failures: int

    __slots__ = ("parameters", "successes", "failures")

    def __init__(self, *, parameters: list[tuple[str, bool]]) -> None:
        self.parameters = {name: ParameterState(name=name, is_required=is_required) for name, is_required in parameters}
        self.successes = 0
        self.failures = 0

    def update(self, *, is_success: bool, applied_parameters: list[str]) -> None:
        if is_success:
            self.successes += 1
        else:
            self.failures += 1

        for parameter in applied_parameters:
            parameter_state = self.parameters[parameter]
            if is_success:
                parameter_state.successes += 1
            else:
                # Split the blame among all used parameters
                parameter_state.failures += 1.0 / len(applied_parameters)


@dataclass
class ParameterState:
    """A single parameter mapping within a link."""

    # "query.user_id", "path_parameters.id", "body.field"
    name: str
    is_required: bool
    successes: int | float
    failures: int | float

    __slots__ = ("name", "is_required", "successes", "failures")

    def __init__(self, *, name: str, is_required: bool) -> None:
        self.name = name
        self.is_required = is_required
        self.successes = 0
        self.failures = 0

    @property
    def use_probability(self) -> float:
        """Calculate probability of using this parameter based on learned success rate."""
        total = self.successes + self.failures

        if total < 3:
            return self._adjust_for_location(INITIAL_SUCCESS_RATE)

        original_success_rate = self.successes / total

        # Aggressive penalty for consistent failure
        if total > MEDIUM_CONFIDENCE_THRESHOLD and self.successes == 0:
            return CONSISTENT_FAILURE_PENALTY

        # Amplify success rate differences with sufficient evidence
        success_rate = original_success_rate**0.7 if total > 50 else original_success_rate

        # Exploration rate (lower for required parameters)
        exploration_rate = BASE_EXPLORATION_RATE * (0.5 if self.is_required else 1.0)

        # Base probability: balance exploitation with exploration
        use_probability = success_rate * (1 - exploration_rate) + (1 - success_rate) * exploration_rate

        # Confidence boost: helps overcome blame from bad partner parameters
        if total > HIGH_CONFIDENCE_THRESHOLD and success_rate > 0.2:
            boost = min(MAX_BOOST, (total - HIGH_CONFIDENCE_THRESHOLD) / 1000)
            use_probability = min(0.95, use_probability + boost)

        # Adaptive minimum based on evidence strength
        minimum = CONSISTENT_FAILURE_PENALTY if total > MEDIUM_CONFIDENCE_THRESHOLD else 0.15

        probability = max(minimum, min(0.95, use_probability))

        return self._adjust_for_location(probability)

    def _adjust_for_location(self, rate: float) -> float:
        # Path parameters are critical for routing - use link values more often
        if self.name.startswith("path_parameters."):
            rate += (1 - rate) / 2
        return rate
