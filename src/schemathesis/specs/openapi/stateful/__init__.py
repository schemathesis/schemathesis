import functools
import operator
from collections import defaultdict
from typing import TYPE_CHECKING, Any, Dict, Generator, List, Tuple, Type

from hypothesis import strategies as st
from hypothesis.stateful import Bundle, Rule, rule
from requests.structures import CaseInsensitiveDict

from ....stateful import APIStateMachine, Direction, StepResult
from ....utils import Ok, combine_strategies
from .. import expressions
from ..links import OpenAPILink
from . import links

if TYPE_CHECKING:
    from ....models import APIOperation, Case
    from ..schemas import BaseOpenAPISchema


APIOperationConnections = Dict[str, List[st.SearchStrategy[Tuple[StepResult, OpenAPILink]]]]


class OpenAPIStateMachine(APIStateMachine):
    def transform(self, result: StepResult, direction: Direction, case: "Case") -> "Case":
        context = expressions.ExpressionContext(case=result.case, response=result.response)
        direction.set_data(case, elapsed=result.elapsed, context=context)
        return case


def create_state_machine(schema: "BaseOpenAPISchema") -> Type[APIStateMachine]:
    """Create a state machine class.

    This state machine will contain transitions that connect some operations' outputs with other operations' inputs.
    """
    bundles = init_bundles(schema)
    connections: APIOperationConnections = defaultdict(list)
    for result in schema.get_all_operations():
        if isinstance(result, Ok):
            links.apply(result.ok(), bundles, connections)

    rules = make_all_rules(schema, bundles, connections)

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
    schema: "BaseOpenAPISchema", bundles: Dict[str, CaseInsensitiveDict], connections: APIOperationConnections
) -> Dict[str, Rule]:
    """Create rules for all API operations, based on the provided connections."""
    return {
        f"rule {operation.verbose_name} {idx}": new
        for operation in (result.ok() for result in schema.get_all_operations() if isinstance(result, Ok))
        for idx, new in enumerate(make_rules(operation, bundles[operation.path][operation.method.upper()], connections))
    }


def make_rules(
    operation: "APIOperation", bundle: Bundle, connections: APIOperationConnections
) -> Generator[Rule, None, None]:
    """Create a rule for an API operation."""

    def _make_rule(previous: st.SearchStrategy) -> Rule:
        decorator = rule(target=bundle, previous=previous, case=operation.as_strategy())  # type: ignore
        return decorator(APIStateMachine._step)

    previous_strategies = connections.get(operation.verbose_name)
    if previous_strategies is not None:
        yield _make_rule(combine_strategies(previous_strategies))
    yield _make_rule(st.none())
