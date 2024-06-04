from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING, Any, Callable, Iterator

from hypothesis import strategies as st
from hypothesis.stateful import Bundle, Rule, precondition, rule

from ....internal.result import Ok
from ....stateful.state_machine import APIStateMachine, Direction, StepResult
from .. import expressions
from ..utils import expand_status_code
from ..links import get_all_links

if TYPE_CHECKING:
    from ....models import Case
    from ....stateful import StateMachineConfig
    from ..schemas import BaseOpenAPISchema

FilterFunction = Callable[["StepResult"], bool]


class OpenAPIStateMachine(APIStateMachine):
    def transform(self, result: StepResult, direction: Direction, case: Case) -> Case:
        context = expressions.ExpressionContext(case=result.case, response=result.response)
        direction.set_data(case, elapsed=result.elapsed, context=context)
        return case


def create_state_machine(
    schema: BaseOpenAPISchema, *, config: StateMachineConfig | None = None
) -> type[APIStateMachine]:
    """Create a state machine class.

    It aims to avoid making calls that are not likely to lead to a stateful call later. For example:
      1. POST /users/
      2. GET /users/{id}/

    This state machine won't make calls to (2) without having a proper response from (1) first.
    """
    operations = [result.ok() for result in schema.get_all_operations() if isinstance(result, Ok)]
    bundles = {operation.verbose_name: Bundle(operation.verbose_name) for operation in operations}
    incoming_transitions = defaultdict(list)
    for operation in operations:
        for _, link in get_all_links(operation):
            target_operation = link.get_target_operation()
            incoming_transitions[target_operation.verbose_name].append(link)
    rules = {}

    for target in operations:
        incoming = incoming_transitions.get(target.verbose_name)
        target_bundle = bundles[target.verbose_name]
        if incoming is not None:
            for link in incoming:
                source = link.operation
                all_status_codes = source.definition.raw["responses"].keys()
                predicate = make_response_filter(link.status_code, all_status_codes)
                rules[f"{source.verbose_name} -> {link.status_code} -> {target.verbose_name}"] = precondition(
                    lambda self, _predicate=predicate: self._has_matching_response(_predicate),
                )(
                    transition(
                        target=target_bundle,
                        previous=bundles[source.verbose_name].filter(predicate),
                        case=target.as_strategy(),
                        link=st.just(link),
                    )
                )
        elif any(
            incoming.operation.verbose_name == target.verbose_name
            for transitions in incoming_transitions.values()
            for incoming in transitions
        ):
            # No incoming transitions, but has at least one outgoing transition
            # For example, POST /users/ -> GET /users/{id}/
            # The source operation has no prerequisite, but we need to allow this rule to be executed
            # in order to reach other transitions
            rules[f"* -> {target.verbose_name}"] = transition(
                target=target_bundle, previous=st.none(), case=target.as_strategy()
            )

    return type(
        "APIWorkflow",
        (OpenAPIStateMachine,),
        {
            "schema": schema,
            "config": config or StateMachineConfig(),
            "bundles": bundles,
            **rules,
        },
    )


def transition(*args: Any, **kwargs: Any) -> Callable[[Callable], Rule]:
    return rule(*args, **kwargs)(APIStateMachine._step)


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

    # This name is displayed in the resulting strategy representation. For example, if you run your tests with
    # `--hypothesis-show-statistics`, then you can see `Bundle(name='GET /users/{user_id}').filter(match_200_response)`
    # which gives you information about the particularly used filter.
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
