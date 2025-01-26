from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import TYPE_CHECKING, Any, Callable, Iterator

from hypothesis import strategies as st
from hypothesis.stateful import Bundle, Rule, precondition, rule

from schemathesis.core.result import Ok
from schemathesis.engine.recorder import ScenarioRecorder
from schemathesis.generation import GenerationMode
from schemathesis.generation.case import Case
from schemathesis.generation.hypothesis import strategies
from schemathesis.generation.stateful.state_machine import APIStateMachine, StepInput, StepOutput, _normalize_name
from schemathesis.schemas import APIOperation
from schemathesis.specs.openapi.links import OpenApiLink, get_all_links
from schemathesis.specs.openapi.stateful.control import TransitionController
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


def collect_transitions(operations: list[APIOperation]) -> ApiTransitions:
    """Collect all transitions between operations."""
    transitions = ApiTransitions()

    for operation in operations:
        for _, link in get_all_links(operation):
            transitions.add_outgoing(operation.label, link)

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

    for target in operations:
        if target.label in transitions.operations:
            incoming = transitions.operations[target.label].incoming
            if incoming:
                for link in incoming:
                    bundle_name = f"{link.source.label} -> {link.status_code}"
                    name = _normalize_name(f"{link.status_code} -> {target.label}")
                    rules[name] = precondition(is_transition_allowed(bundle_name, link.source.label, target.label))(
                        transition(
                            name=name,
                            target=catch_all,
                            input=bundles[bundle_name].flatmap(
                                into_step_input(target=target, link=link, modes=schema.generation_config.modes)
                            ),
                        )
                    )
            elif transitions.operations[target.label].outgoing:
                # No incoming transitions, but has at least one outgoing transition
                # For example, POST /users/ -> GET /users/{id}/
                # The source operation has no prerequisite, but we need to allow this rule to be executed
                # in order to reach other transitions
                name = _normalize_name(f"{target.label} -> X")
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


def into_step_input(
    target: APIOperation, link: OpenApiLink, modes: list[GenerationMode]
) -> Callable[[StepOutput], st.SearchStrategy[StepInput]]:
    def builder(_output: StepOutput) -> st.SearchStrategy[StepInput]:
        @st.composite  # type: ignore[misc]
        def inner(draw: st.DrawFn, output: StepOutput) -> StepInput:
            transition_data = link.extract(output)

            kwargs: dict[str, Any] = {
                container: {
                    name: extracted.value.ok()
                    for name, extracted in data.items()
                    if isinstance(extracted.value, Ok) and extracted.value.ok() is not None
                }
                for container, data in transition_data.parameters.items()
            }
            if (
                transition_data.request_body is not None
                and isinstance(transition_data.request_body.value, Ok)
                and not link.merge_body
            ):
                kwargs["body"] = transition_data.request_body.value.ok()
            cases = strategies.combine([target.as_strategy(generation_mode=mode, **kwargs) for mode in modes])
            case = draw(cases)
            if (
                transition_data.request_body is not None
                and isinstance(transition_data.request_body.value, Ok)
                and link.merge_body
            ):
                new = transition_data.request_body.value.ok()
                if isinstance(case.body, dict) and isinstance(new, dict):
                    case.body = {**case.body, **new}
                else:
                    case.body = new
            return StepInput(case=case, transition=transition_data)

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
