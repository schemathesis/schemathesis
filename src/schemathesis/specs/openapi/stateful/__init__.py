from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import TYPE_CHECKING, Any, Callable, Iterator

from hypothesis import strategies as st
from hypothesis.stateful import Bundle, Rule, precondition, rule

from schemathesis.core.errors import InvalidStateMachine
from schemathesis.core.result import Ok
from schemathesis.core.transforms import UNRESOLVABLE
from schemathesis.engine.recorder import ScenarioRecorder
from schemathesis.generation import GenerationMode
from schemathesis.generation.case import Case
from schemathesis.generation.hypothesis import strategies
from schemathesis.generation.stateful.state_machine import APIStateMachine, StepInput, StepOutput, _normalize_name
from schemathesis.schemas import APIOperation
from schemathesis.specs.openapi.stateful.control import TransitionController
from schemathesis.specs.openapi.stateful.links import OpenApiLink, get_all_links
from schemathesis.specs.openapi.utils import expand_status_code

if TYPE_CHECKING:
    from schemathesis.generation.stateful.state_machine import StepOutput
    from schemathesis.specs.openapi.schemas import BaseOpenAPISchema

FilterFunction = Callable[["StepOutput"], bool]


class OpenAPIStateMachine(APIStateMachine):
    _response_matchers: dict[str, Callable[[StepOutput], str | None]]
    _transitions: ApiTransitions

    def __init__(self) -> None:
        self.recorder = ScenarioRecorder(label="Stateful tests")
        self.control = TransitionController(self._transitions)
        super().__init__()

    def _get_target_for_result(self, result: StepOutput) -> str | None:
        matcher = self._response_matchers.get(result.case.operation.label)
        if matcher is None:
            return None
        return matcher(result)


# The proportion of negative tests generated for "root" transitions
NEGATIVE_TEST_CASES_THRESHOLD = 10


@dataclass
class OperationTransitions:
    """Transitions for a single operation."""

    __slots__ = ("incoming", "outgoing")

    def __init__(self) -> None:
        self.incoming: list[OpenApiLink] = []
        self.outgoing: list[OpenApiLink] = []


@dataclass
class ApiTransitions:
    """Stores all transitions grouped by operation."""

    __slots__ = ("operations",)

    def __init__(self) -> None:
        # operation label -> its transitions
        self.operations: dict[str, OperationTransitions] = {}

    def add_outgoing(self, source: str, link: OpenApiLink) -> None:
        """Record an outgoing transition from source operation."""
        self.operations.setdefault(source, OperationTransitions()).outgoing.append(link)
        self.operations.setdefault(link.target.label, OperationTransitions()).incoming.append(link)


@dataclass
class RootTransitions:
    """Classification of API operations that can serve as entry points."""

    __slots__ = ("reliable", "fallback")

    def __init__(self) -> None:
        # Operations likely to succeed and provide data for other transitions
        self.reliable: set[str] = set()
        # Operations that might work but are less reliable
        self.fallback: set[str] = set()


def collect_transitions(operations: list[APIOperation]) -> ApiTransitions:
    """Collect all transitions between operations."""
    transitions = ApiTransitions()

    selected_labels = {operation.label for operation in operations}
    errors = []
    for operation in operations:
        for _, link in get_all_links(operation):
            if isinstance(link, Ok):
                if link.ok().target.label in selected_labels:
                    transitions.add_outgoing(operation.label, link.ok())
            else:
                errors.append(link.err())

    if errors:
        raise InvalidStateMachine(errors)

    return transitions


def create_state_machine(schema: BaseOpenAPISchema) -> type[APIStateMachine]:
    operations = [result.ok() for result in schema.get_all_operations() if isinstance(result, Ok)]
    bundles = {}
    transitions = collect_transitions(operations)
    _response_matchers: dict[str, Callable[[StepOutput], str | None]] = {}

    # Create bundles and matchers
    for operation in operations:
        all_status_codes = tuple(operation.definition.raw["responses"])
        bundle_matchers = []

        if operation.label in transitions.operations:
            # Use outgoing transitions
            for link in transitions.operations[operation.label].outgoing:
                bundle_name = f"{operation.label} -> {link.status_code}"
                bundles[bundle_name] = Bundle(bundle_name)
                bundle_matchers.append((bundle_name, make_response_filter(link.status_code, all_status_codes)))

        if bundle_matchers:
            _response_matchers[operation.label] = make_response_matcher(bundle_matchers)

    rules = {}
    catch_all = Bundle("catch_all")

    # We want stateful testing to be effective and focus on meaningful transitions.
    # An operation is considered as a "root" transition (entry point) if it satisfies certain criteria
    # that indicate it's likely to succeed and provide data for other transitions.
    # For example:
    #   - POST operations that create resources
    #   - GET operations without path parameters (e.g., GET /users/ to list all users)
    #
    # We avoid adding operations as roots if they:
    #   1. Have incoming transitions that will provide proper data
    #      Example: If POST /users/ -> GET /users/{id} exists, we don't need
    #      to generate random user IDs for GET /users/{id}
    #   2. Are unlikely to succeed with random data
    #      Example: GET /users/{id} with random ID is likely to return 404
    #
    # This way we:
    #   1. Maximize the chance of successful transitions
    #   2. Don't waste the test budget (limited number of steps) on likely-to-fail operations
    #   3. Focus on transitions that are designed to work together via links

    roots = classify_root_transitions(operations, transitions)

    for target in operations:
        if target.label in transitions.operations:
            incoming = transitions.operations[target.label].incoming
            if incoming:
                for link in incoming:
                    bundle_name = f"{link.source.label} -> {link.status_code}"
                    name = _normalize_name(
                        f"{link.source.label} -> {link.status_code} -> {link.name} -> {target.label}"
                    )
                    assert name not in rules, name
                    rules[name] = precondition(is_transition_allowed(bundle_name, link.source.label, target.label))(
                        transition(
                            name=name,
                            target=catch_all,
                            input=bundles[bundle_name].flatmap(
                                into_step_input(target=target, link=link, modes=schema.generation_config.modes)
                            ),
                        )
                    )
            if target.label in roots.reliable or (not roots.reliable and target.label in roots.fallback):
                name = _normalize_name(f"RANDOM -> {target.label}")
                if len(schema.generation_config.modes) == 1:
                    case_strategy = target.as_strategy(generation_mode=schema.generation_config.modes[0])
                else:
                    _strategies = {
                        method: target.as_strategy(generation_mode=method) for method in schema.generation_config.modes
                    }

                    @st.composite  # type: ignore[misc]
                    def case_strategy_factory(
                        draw: st.DrawFn, strategies: dict[GenerationMode, st.SearchStrategy] = _strategies
                    ) -> Case:
                        if draw(st.integers(min_value=0, max_value=99)) < NEGATIVE_TEST_CASES_THRESHOLD:
                            return draw(strategies[GenerationMode.NEGATIVE])
                        return draw(strategies[GenerationMode.POSITIVE])

                    case_strategy = case_strategy_factory()

                rules[name] = precondition(is_root_allowed(target.label))(
                    transition(name=name, target=catch_all, input=case_strategy.map(StepInput.initial))
                )

    return type(
        "APIWorkflow",
        (OpenAPIStateMachine,),
        {
            "schema": schema,
            "bundles": bundles,
            "_response_matchers": _response_matchers,
            "_transitions": transitions,
            **rules,
        },
    )


def classify_root_transitions(operations: list[APIOperation], transitions: ApiTransitions) -> RootTransitions:
    """Find operations that can serve as root transitions."""
    roots = RootTransitions()

    for operation in operations:
        # Skip if operation has no outgoing transitions
        operation_transitions = transitions.operations.get(operation.label)
        if not operation_transitions or not operation_transitions.outgoing:
            continue

        if is_likely_root_transition(operation, operation_transitions):
            roots.reliable.add(operation.label)
        else:
            roots.fallback.add(operation.label)

    return roots


def is_likely_root_transition(operation: APIOperation, transitions: OperationTransitions) -> bool:
    """Check if operation is likely to succeed as a root transition."""
    # POST operations with request bodies are likely to create resources
    if operation.method == "post" and operation.body:
        return True

    # GET operations without path parameters are likely to return lists
    if operation.method == "get" and not operation.path_parameters:
        return True

    return False


def into_step_input(
    target: APIOperation, link: OpenApiLink, modes: list[GenerationMode]
) -> Callable[[StepOutput], st.SearchStrategy[StepInput]]:
    def builder(_output: StepOutput) -> st.SearchStrategy[StepInput]:
        @st.composite  # type: ignore[misc]
        def inner(draw: st.DrawFn, output: StepOutput) -> StepInput:
            transition = link.extract(output)

            kwargs: dict[str, Any] = {
                container: {
                    name: extracted.value.ok()
                    for name, extracted in data.items()
                    if isinstance(extracted.value, Ok) and extracted.value.ok() not in (None, UNRESOLVABLE)
                }
                for container, data in transition.parameters.items()
            }

            if (
                transition.request_body is not None
                and isinstance(transition.request_body.value, Ok)
                and transition.request_body.value.ok() is not UNRESOLVABLE
                and not link.merge_body
            ):
                kwargs["body"] = transition.request_body.value.ok()
            cases = strategies.combine([target.as_strategy(generation_mode=mode, **kwargs) for mode in modes])
            case = draw(cases)
            if (
                transition.request_body is not None
                and isinstance(transition.request_body.value, Ok)
                and transition.request_body.value.ok() is not UNRESOLVABLE
                and link.merge_body
            ):
                new = transition.request_body.value.ok()
                if isinstance(case.body, dict) and isinstance(new, dict):
                    case.body = {**case.body, **new}
                else:
                    case.body = new
            return StepInput(case=case, transition=transition)

        return inner(output=_output)

    return builder


def is_transition_allowed(bundle_name: str, source: str, target: str) -> Callable[[OpenAPIStateMachine], bool]:
    def inner(machine: OpenAPIStateMachine) -> bool:
        return bool(machine.bundles.get(bundle_name)) and machine.control.allow_transition(source, target)

    return inner


def is_root_allowed(label: str) -> Callable[[OpenAPIStateMachine], bool]:
    def inner(machine: OpenAPIStateMachine) -> bool:
        return machine.control.allow_root_transition(label, machine.bundles)

    return inner


def transition(*, name: str, target: Bundle, input: st.SearchStrategy[StepInput]) -> Callable[[Callable], Rule]:
    def step_function(self: OpenAPIStateMachine, input: StepInput) -> StepOutput | None:
        if input.transition is not None:
            self.recorder.record_case(
                parent_id=input.transition.parent_id, transition=input.transition, case=input.case
            )
        else:
            self.recorder.record_case(parent_id=None, transition=None, case=input.case)
        self.control.record_step(input, self.recorder)
        return APIStateMachine._step(self, input=input)

    step_function.__name__ = name

    return rule(target=target, input=input)(step_function)


def make_response_matcher(matchers: list[tuple[str, FilterFunction]]) -> Callable[[StepOutput], str | None]:
    def compare(result: StepOutput) -> str | None:
        for bundle_name, response_filter in matchers:
            if response_filter(result):
                return bundle_name
        return None

    return compare


@lru_cache
def make_response_filter(status_code: str, all_status_codes: Iterator[str]) -> FilterFunction:
    """Create a filter for stored responses.

    This filter will decide whether some response is suitable to use as a source for requesting some API operation.
    """
    if status_code == "default":
        return default_status_code(all_status_codes)
    return match_status_code(status_code)


def match_status_code(status_code: str) -> FilterFunction:
    """Create a filter function that matches all responses with the given status code.

    Note that the status code can contain "X", which means any digit.
    For example, 50X will match all status codes from 500 to 509.
    """
    status_codes = set(expand_status_code(status_code))

    def compare(result: StepOutput) -> bool:
        return result.response.status_code in status_codes

    compare.__name__ = f"match_{status_code}_response"

    return compare


def default_status_code(status_codes: Iterator[str]) -> FilterFunction:
    """Create a filter that matches all "default" responses.

    In Open API, the "default" response is the one that is used if no other options were matched.
    Therefore, we need to match only responses that were not matched by other listed status codes.
    """
    expanded_status_codes = {
        status_code for value in status_codes if value != "default" for status_code in expand_status_code(value)
    }

    def match_default_response(result: StepOutput) -> bool:
        return result.response.status_code not in expanded_status_codes

    return match_default_response
