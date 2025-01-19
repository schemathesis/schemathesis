from __future__ import annotations

from collections import defaultdict
from functools import lru_cache
from typing import TYPE_CHECKING, Any, Callable, Iterator

from hypothesis import strategies as st
from hypothesis.stateful import Bundle, Rule, precondition, rule

from schemathesis.core.result import Ok
from schemathesis.generation.case import Case
from schemathesis.generation.hypothesis import strategies
from schemathesis.generation.stateful.state_machine import APIStateMachine, StepInput, StepOutput, _normalize_name
from schemathesis.schemas import APIOperation

from ....generation import GenerationMode
from ..links import OpenApiLink, get_all_links
from ..utils import expand_status_code

if TYPE_CHECKING:
    from schemathesis.generation.stateful.state_machine import StepOutput

    from ..schemas import BaseOpenAPISchema

FilterFunction = Callable[["StepOutput"], bool]


class OpenAPIStateMachine(APIStateMachine):
    _response_matchers: dict[str, Callable[[StepOutput], str | None]]

    def _get_target_for_result(self, result: StepOutput) -> str | None:
        matcher = self._response_matchers.get(result.case.operation.label)
        if matcher is None:
            return None
        return matcher(result)


# The proportion of negative tests generated for "root" transitions
NEGATIVE_TEST_CASES_THRESHOLD = 20


def create_state_machine(schema: BaseOpenAPISchema) -> type[APIStateMachine]:
    """Create a state machine class.

    It aims to avoid making calls that are not likely to lead to a stateful call later. For example:
      1. POST /users/
      2. GET /users/{id}/

    This state machine won't make calls to (2) without having a proper response from (1) first.
    """
    operations = [result.ok() for result in schema.get_all_operations() if isinstance(result, Ok)]
    bundles = {}
    incoming_transitions = defaultdict(list)
    _response_matchers: dict[str, Callable[[StepOutput], str | None]] = {}
    # Statistic structure follows the links and count for each response status code
    for operation in operations:
        all_status_codes = tuple(operation.definition.raw["responses"])
        bundle_matchers = []
        for _, link in get_all_links(operation):
            bundle_name = f"{operation.label} -> {link.status_code}"
            bundles[bundle_name] = Bundle(bundle_name)
            incoming_transitions[link.target.label].append(link)
            bundle_matchers.append((bundle_name, make_response_filter(link.status_code, all_status_codes)))
        if bundle_matchers:
            _response_matchers[operation.label] = make_response_matcher(bundle_matchers)
    rules = {}
    catch_all = Bundle("catch_all")

    for target in operations:
        incoming = incoming_transitions.get(target.label)
        if incoming is not None:
            for link in incoming:
                bundle_name = f"{link.source.label} -> {link.status_code}"
                name = _normalize_name(f"{link.status_code} -> {target.label}")
                rules[name] = precondition(ensure_non_empty_bundle(bundle_name))(
                    transition(
                        name=name,
                        target=catch_all,
                        input=bundles[bundle_name].flatmap(
                            into_step_input(target=target, link=link, modes=schema.generation_config.modes)
                        ),
                    )
                )
        elif any(
            incoming.source.label == target.label
            for transitions in incoming_transitions.values()
            for incoming in transitions
        ):
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

            rules[name] = precondition(ensure_links_followed)(
                transition(name=name, target=catch_all, input=case_strategy.map(StepInput.initial))
            )

    return type(
        "APIWorkflow",
        (OpenAPIStateMachine,),
        {
            "schema": schema,
            "bundles": bundles,
            "_response_matchers": _response_matchers,
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


def ensure_non_empty_bundle(bundle_name: str) -> Callable[[APIStateMachine], bool]:
    def inner(machine: APIStateMachine) -> bool:
        return bool(machine.bundles.get(bundle_name))

    return inner


def ensure_links_followed(machine: APIStateMachine) -> bool:
    # If there are responses that have links to follow, reject any rule without incoming transitions
    for bundle in machine.bundles.values():
        if bundle:
            return False
    return True


def transition(*, name: str, target: Bundle, input: st.SearchStrategy[StepInput]) -> Callable[[Callable], Rule]:
    def step_function(self: APIStateMachine, input: StepInput) -> StepOutput | None:
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
