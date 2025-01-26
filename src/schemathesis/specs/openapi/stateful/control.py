from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING

from schemathesis.engine.recorder import ScenarioRecorder
from schemathesis.generation.stateful.state_machine import DEFAULT_STATEFUL_STEP_COUNT

if TYPE_CHECKING:
    from requests.structures import CaseInsensitiveDict

    from schemathesis.generation.stateful.state_machine import StepInput
    from schemathesis.specs.openapi.stateful import ApiTransitions


# It is enough to be able to catch double-click type of issues
MAX_OPERATIONS_PER_SOURCE_CAP = 2
# Maximum number of concurrent root sources (e.g., active users in the system)
MAX_ROOT_SOURCES = 2


def _get_max_operations_per_source(transitions: ApiTransitions) -> int:
    """Calculate global limit based on number of sources to maximize diversity of used API calls."""
    sources = len(transitions.operations)

    if sources == 0:
        return MAX_OPERATIONS_PER_SOURCE_CAP

    # Total steps divided by number of sources, but never below the cap
    return max(MAX_OPERATIONS_PER_SOURCE_CAP, DEFAULT_STATEFUL_STEP_COUNT // sources)


@dataclass
class TransitionController:
    """Controls which transitions can be executed in a state machine."""

    __slots__ = ("transitions", "max_operations_per_source", "statistic")

    def __init__(self, transitions: ApiTransitions) -> None:
        # Incoming & outgoing transitions available in the state machine
        self.transitions = transitions
        self.max_operations_per_source = _get_max_operations_per_source(transitions)
        # source -> derived API calls
        self.statistic: dict[str, dict[str, Counter[str]]] = {}

    def record_step(self, input: StepInput, recorder: ScenarioRecorder) -> None:
        """Record API call input."""
        case = input.case

        if (
            case.operation.label in self.transitions.operations
            and self.transitions.operations[case.operation.label].outgoing
        ):
            # This API operation has outgoing transitions, hence record it as a source
            entry = self.statistic.setdefault(input.case.operation.label, {})
            entry[input.case.id] = Counter()

        if input.transition is not None:
            # Find immediate parent and record as derived operation
            parent = recorder.cases[input.transition.parent_id]
            source = parent.value.operation.label
            case_id = parent.value.id

            if source in self.statistic and case_id in self.statistic[source]:
                self.statistic[source][case_id][case.operation.label] += 1

    def allow_root_transition(self, source: str, bundles: dict[str, CaseInsensitiveDict]) -> bool:
        """Decide if this root transition should be allowed now."""
        if len(self.statistic.get(source, {})) < MAX_ROOT_SOURCES:
            return True

        # If all non-root operations are blocked, then allow root ones to make progress
        history = {name.split("->")[0].strip() for name, values in bundles.items() if values}
        return all(
            incoming.source.label not in history
            or not self.allow_transition(incoming.source.label, incoming.target.label)
            for transitions in self.transitions.operations.values()
            for incoming in transitions.incoming
            if transitions.incoming
        )

    def allow_transition(self, source: str, target: str) -> bool:
        """Decide if this transition should be allowed now."""
        existing = self.statistic.get(source, {})
        total = sum(metric.get(target, 0) for metric in existing.values())
        return total < self.max_operations_per_source
