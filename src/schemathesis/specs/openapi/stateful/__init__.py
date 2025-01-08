from __future__ import annotations

from collections import defaultdict
from functools import lru_cache
from typing import TYPE_CHECKING, Any, Callable, Iterator

from hypothesis import strategies as st
from hypothesis.stateful import Bundle, Rule, precondition, rule

from schemathesis.core import NOT_SET, NotSet
from schemathesis.core.result import Ok
from schemathesis.generation.hypothesis import strategies
from schemathesis.generation.stateful.state_machine import APIStateMachine, Direction, StepResult, _normalize_name

from ....generation import GenerationMode
from .. import expressions
from ..links import get_all_links
from ..utils import expand_status_code

if TYPE_CHECKING:
    from schemathesis.generation.case import Case
    from schemathesis.generation.stateful.state_machine import StepResult

    from ..schemas import BaseOpenAPISchema

FilterFunction = Callable[["StepResult"], bool]


class OpenAPIStateMachine(APIStateMachine):
    _response_matchers: dict[str, Callable[[StepResult], str | None]]

    def _get_target_for_result(self, result: StepResult) -> str | None:
        matcher = self._response_matchers.get(result.case.operation.label)
        if matcher is None:
            return None
        return matcher(result)

    def transform(self, result: StepResult, direction: Direction, case: Case) -> Case:
        context = expressions.ExpressionContext(case=result.case, response=result.response)
        direction.set_data(case, context=context)
        return case


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
    _response_matchers: dict[str, Callable[[StepResult], str | None]] = {}
    # Statistic structure follows the links and count for each response status code
    for operation in operations:
        all_status_codes = tuple(operation.definition.raw["responses"])
        bundle_matchers = []
        for _, link in get_all_links(operation):
            bundle_name = f"{operation.label} -> {link.status_code}"
            bundles[bundle_name] = Bundle(bundle_name)
            target_operation = link.get_target_operation()
            incoming_transitions[target_operation.label].append(link)
            bundle_matchers.append((bundle_name, make_response_filter(link.status_code, all_status_codes)))
        if bundle_matchers:
            _response_matchers[operation.label] = make_response_matcher(bundle_matchers)
    rules = {}
    catch_all = Bundle("catch_all")

    for target in operations:
        incoming = incoming_transitions.get(target.label)
        if incoming is not None:
            for link in incoming:
                source = link.operation
                bundle_name = f"{source.label} -> {link.status_code}"
                name = _normalize_name(f"{target.label} -> {link.status_code}")
                case_strategy = strategies.combine(
                    [target.as_strategy(generation_mode=mode) for mode in schema.generation_config.modes]
                )
                bundle = bundles[bundle_name]
                rules[name] = transition(
                    name=name,
                    target=catch_all,
                    previous=bundle,
                    case=case_strategy,
                    link=st.just(link),
                )
        elif any(
            incoming.operation.label == target.label
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
                transition(
                    name=name,
                    target=catch_all,
                    previous=st.none(),
                    case=case_strategy,
                )
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


def ensure_links_followed(machine: APIStateMachine) -> bool:
    # If there are responses that have links to follow, reject any rule without incoming transitions
    for bundle in machine.bundles.values():
        if bundle:
            return False
    return True


def transition(
    *,
    name: str,
    target: Bundle,
    previous: Bundle | st.SearchStrategy,
    case: st.SearchStrategy,
    link: st.SearchStrategy | NotSet = NOT_SET,
) -> Callable[[Callable], Rule]:
    def step_function(*args_: Any, **kwargs_: Any) -> StepResult | None:
        return APIStateMachine._step(*args_, **kwargs_)

    step_function.__name__ = name

    kwargs = {"target": target, "previous": previous, "case": case}
    if not isinstance(link, NotSet):
        kwargs["link"] = link

    return rule(**kwargs)(step_function)


def make_response_matcher(matchers: list[tuple[str, FilterFunction]]) -> Callable[[StepResult], str | None]:
    def compare(result: StepResult) -> str | None:
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

    def compare(result: StepResult) -> bool:
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

    def match_default_response(result: StepResult) -> bool:
        return result.response.status_code not in expanded_status_codes

    return match_default_response
