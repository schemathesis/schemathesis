from collections import defaultdict
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Type, cast

from hypothesis import strategies as st
from hypothesis.stateful import Bundle, Rule, precondition, rule
from requests.structures import CaseInsensitiveDict

from ....stateful import APIStateMachine, Direction, StepResult
from ....utils import Ok, combine_strategies
from .. import expressions
from . import links
from .links import APIOperationConnections, Connection, _convert_strategy, apply, make_response_filter

if TYPE_CHECKING:
    from ....models import APIOperation, Case
    from ..schemas import BaseOpenAPISchema


class OpenAPIStateMachine(APIStateMachine):
    def transform(self, result: StepResult, direction: Direction, case: "Case") -> "Case":
        context = expressions.ExpressionContext(case=result.case, response=result.response)
        direction.set_data(case, elapsed=result.elapsed, context=context)
        return case


def create_state_machine(schema: "BaseOpenAPISchema") -> Type[APIStateMachine]:
    """Create a state machine class.

    It aims to avoid making calls that are not likely to lead to a stateful call later. For example:
      1. POST /users/
      2. GET /users/{id}/

    This state machine won't make calls to (2) without having a proper response from (1) first.
    """
    # Bundles are special strategies, allowing us to draw responses from previous calls
    bundles = init_bundles(schema)
    connections: APIOperationConnections = defaultdict(list)
    operations = [result.ok() for result in schema.get_all_operations() if isinstance(result, Ok)]
    for operation in operations:
        apply(operation, bundles, connections)

    rules = make_all_rules(operations, bundles, connections)

    kwargs: Dict[str, Any] = {"bundles": bundles, "schema": schema}
    return type("APIWorkflow", (OpenAPIStateMachine,), {**kwargs, **rules})


def init_bundles(schema: "BaseOpenAPISchema") -> Dict[str, CaseInsensitiveDict]:
    """Create bundles for all operations in the given schema.

    Each API operation has a bundle that stores all responses from that operation.
    We need to create bundles first, so they can be referred when building connections between operations.
    """
    output: Dict[str, CaseInsensitiveDict] = {}
    for result in schema.get_all_operations():
        if isinstance(result, Ok):
            operation = result.ok()
            output.setdefault(operation.path, CaseInsensitiveDict())
            output[operation.path][operation.method.upper()] = Bundle(operation.verbose_name)  # type: ignore
    return output


def make_all_rules(
    operations: List["APIOperation"],
    bundles: Dict[str, CaseInsensitiveDict],
    connections: APIOperationConnections,
) -> Dict[str, Rule]:
    """Create rules for all API operations, based on the provided connections."""
    rules = {}
    for operation in operations:
        new_rule = make_rule(operation, bundles[operation.path][operation.method.upper()], connections)
        if new_rule is not None:
            rules[f"rule {operation.verbose_name}"] = new_rule
    return rules


def make_rule(
    operation: "APIOperation",
    bundle: Bundle,
    connections: APIOperationConnections,
) -> Optional[Rule]:
    """Create a rule for an API operation."""

    def _make_rule(previous: st.SearchStrategy) -> Rule:
        decorator = rule(target=bundle, previous=previous, case=operation.as_strategy())  # type: ignore
        return decorator(APIStateMachine._step)

    incoming = connections.get(operation.verbose_name)
    if incoming is not None:
        incoming_connections = cast(List[Connection], incoming)
        strategies = [connection.strategy for connection in incoming_connections]
        _rule = _make_rule(combine_strategies(strategies))

        def has_source_response(self: OpenAPIStateMachine) -> bool:
            # To trigger this transition, there should be matching responses from the source operations
            return any(connection.source in self.bundles for connection in incoming_connections)

        return precondition(has_source_response)(_rule)
    # No incoming transitions - make rules only for operations that have at least one outgoing transition
    if any(
        connection.source == operation.verbose_name
        for operation_connections in connections.values()
        for connection in operation_connections
    ):
        return _make_rule(st.none())
    return None
