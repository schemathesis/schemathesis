from __future__ import annotations

from dataclasses import dataclass

# When there is not enough data, be optimistic
INITIAL_SUCCESS_RATE = 0.7
BASE_EXPLORATION_RATE = 0.15


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

    # "query.user_id", "path.id", "body.field"
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
    def success_rate(self) -> float:
        total = self.successes + self.failures
        if total < 3:
            return INITIAL_SUCCESS_RATE
        return self.successes / total

    @property
    def use_probability(self) -> float:
        """Calculate probability of using this parameter based on learned success rate."""
        total = self.successes + self.failures
        if total < 3:
            success_rate = INITIAL_SUCCESS_RATE
        else:
            success_rate = self.successes / total

        # Probability of NOT using the parameter
        exploration_rate = BASE_EXPLORATION_RATE

        # Adjust based on parameter characteristics
        if self.is_required:
            # Less exploration for required parameters
            exploration_rate *= 0.5

        # Combine learned success rate with exploration
        # High success rate + low exploration = high usage probability
        use_probability = success_rate * (1 - exploration_rate) + (1 - success_rate) * exploration_rate

        # Clamp to reasonable bounds
        return max(0.2, min(0.95, use_probability))
