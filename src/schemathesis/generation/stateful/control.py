from __future__ import annotations

from collections import Counter
from collections.abc import Collection, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

from schemathesis.core import DEFAULT_MAX_SCENARIO_STEPS
from schemathesis.engine.recorder import ScenarioRecorder
from schemathesis.engine.supervisor import SchedulingDirective

if TYPE_CHECKING:
    from requests.structures import CaseInsensitiveDict

    from schemathesis.engine.supervisor import Supervisor
    from schemathesis.generation.stateful.state_machine import StepInput


class _Endpoint(Protocol):
    @property
    def label(self) -> str: ...  # pragma: no cover


class _Edge(Protocol):
    @property
    def source(self) -> _Endpoint: ...  # pragma: no cover

    @property
    def target(self) -> _Endpoint: ...  # pragma: no cover


class _OperationTransitions(Protocol):
    @property
    def incoming(self) -> Sequence[_Edge]: ...  # pragma: no cover

    @property
    def outgoing(self) -> Sequence[_Edge]: ...  # pragma: no cover


class Transitions(Protocol):
    """Spec-agnostic transition graph contract."""

    @property
    def operations(self) -> Mapping[str, _OperationTransitions]: ...  # pragma: no cover

    def producer_labels_for_bundle(self, bundle_name: str) -> Iterable[str]:  # pragma: no cover
        """Yield operation labels that emit values into the named bundle."""
        ...


@dataclass(slots=True)
class TransitionEndpoint:
    label: str


@dataclass(slots=True)
class TransitionEdge:
    source: TransitionEndpoint
    target: TransitionEndpoint


@dataclass(slots=True)
class OperationTransitions:
    incoming: list[TransitionEdge] = field(default_factory=list)
    outgoing: list[TransitionEdge] = field(default_factory=list)


@dataclass(slots=True)
class BundleTransitions:
    """Transition graph of a state machine that passes values between operations through bundles."""

    operations: dict[str, OperationTransitions]
    # Bundle name -> producer operation labels emitting into that bundle.
    bundle_sources: dict[str, list[str]]

    def producer_labels_for_bundle(self, bundle_name: str) -> Iterator[str]:
        yield from self.bundle_sources.get(bundle_name, ())


# It is enough to be able to catch double-click type of issues
MAX_OPERATIONS_PER_SOURCE_CAP = 2
# Maximum number of concurrent root sources (e.g., active users in the system)
MAX_ROOT_SOURCES = 2


def _scenario_step_count() -> int:
    """Steps per scenario configured for the Hypothesis run that creates the state machine."""
    import hypothesis

    # A running Hypothesis test exposes its own settings as the default ones.
    current = hypothesis.settings.default
    if current is None:
        return DEFAULT_MAX_SCENARIO_STEPS
    return current.stateful_step_count


def _get_max_operations_per_source(transitions: Transitions) -> int:
    """Calculate global limit based on number of sources to maximize diversity of used API calls."""
    sources = len(transitions.operations)

    if sources == 0:
        return MAX_OPERATIONS_PER_SOURCE_CAP

    # Total steps divided by number of sources, but never below the cap
    return max(MAX_OPERATIONS_PER_SOURCE_CAP, _scenario_step_count() // sources)


@dataclass
class TransitionController:
    """Controls which transitions can be executed in a state machine."""

    __slots__ = ("transitions", "roots", "max_operations_per_source", "statistic", "productive_roots", "supervisor")

    def __init__(self, transitions: Transitions, roots: Collection[str]) -> None:
        # Incoming & outgoing transitions available in the state machine
        self.transitions = transitions
        # Labels of operations that can start a chain without data from earlier steps
        self.roots = roots
        self.max_operations_per_source = _get_max_operations_per_source(transitions)
        # source -> derived API calls
        self.statistic: dict[str, dict[str, Counter[str]]] = {}
        # Roots whose responses gave later steps a value to use in this scenario
        self.productive_roots: set[str] = set()
        # Optional engine-side supervisor; the engine sets this on the per-scenario
        # state machine so transitions targeting SKIP-verdict operations are filtered out.
        self.supervisor: Supervisor | None = None

    def _is_skipped(self, label: str) -> bool:
        return self.supervisor is not None and self.supervisor.verdict(label).directive is SchedulingDirective.SKIP

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

    def record_productive_root(self, label: str) -> None:
        """Record that a root call returned data that later steps can use."""
        self.productive_roots.add(label)

    def _root_calls(self, label: str) -> int:
        return len(self.statistic.get(label, {}))

    def allow_root_transition(self, source: str, bundles: dict[str, CaseInsensitiveDict]) -> bool:
        """Decide if this root transition should be allowed now."""
        if self._is_skipped(source):
            return False
        if self._root_calls(source) < MAX_ROOT_SOURCES:
            return True

        # If all non-root operations are blocked, then allow root ones to make progress
        history: set[str] = set()
        for name, values in bundles.items():
            if values:
                history.update(self.transitions.producer_labels_for_bundle(name))
        if not all(
            incoming.source.label not in history
            or not self.allow_transition(incoming.source.label, incoming.target.label)
            for transitions in self.transitions.operations.values()
            for incoming in transitions.incoming
            if transitions.incoming
        ):
            return False
        if source in self.productive_roots:
            return True
        # Calling a root again after it gave later steps nothing tends to repeat the same dead end,
        # so prefer any other root and spread calls evenly once every root is in that state.
        available = [root for root in self.roots if not self._is_skipped(root)]
        if any(self._root_calls(root) < MAX_ROOT_SOURCES or root in self.productive_roots for root in available):
            return False
        return self._root_calls(source) == min(self._root_calls(root) for root in available)

    def allow_transition(self, source: str, target: str) -> bool:
        """Decide if this transition should be allowed now."""
        if self._is_skipped(target):
            return False
        existing = self.statistic.get(source, {})
        total = sum(metric.get(target, 0) for metric in existing.values())
        return total < self.max_operations_per_source
