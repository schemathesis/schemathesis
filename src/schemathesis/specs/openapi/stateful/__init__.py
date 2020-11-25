import functools
import operator
from collections import defaultdict
from typing import TYPE_CHECKING, Any, Dict, List, Tuple, Type

from hypothesis.stateful import Bundle, Rule, rule
from hypothesis.strategies import SearchStrategy, none
from requests.structures import CaseInsensitiveDict

from ....stateful import APIStateMachine, Direction, StepResult
from .. import expressions
from ..links import OpenAPILink
from . import links

if TYPE_CHECKING:
    from ....models import Case, Endpoint
    from ..schemas import BaseOpenAPISchema


EndpointConnections = Dict[str, List[SearchStrategy[Tuple[StepResult, OpenAPILink]]]]


class OpenAPIStateMachine(APIStateMachine):
    def transform(self, result: StepResult, direction: Direction, case: "Case") -> "Case":
        context = expressions.ExpressionContext(case=result.case, response=result.response)
        direction.set_data(case, context=context)
        return case


def create_state_machine(schema: "BaseOpenAPISchema") -> Type[APIStateMachine]:
    """Create a state machine class.

    This state machine will contain transitions that connect some endpoints' outputs with other endpoints' inputs.
    """
    bundles = init_bundles(schema)
    connections: EndpointConnections = defaultdict(list)
    for endpoint in schema.get_all_endpoints():
        links.apply(endpoint, bundles, connections)

    rules = make_all_rules(schema, bundles, connections)

    kwargs: Dict[str, Any] = {"bundles": bundles, "schema": schema}
    return type("APIWorkflow", (OpenAPIStateMachine,), {**kwargs, **rules})


def init_bundles(schema: "BaseOpenAPISchema") -> Dict[str, CaseInsensitiveDict]:
    """Create bundles for all endpoints in the given schema.

    Each endpoint has a bundle that stores all responses from that endpoint.
    We need to create bundles first, so they can be referred when building connections between endpoints.
    """
    output: Dict[str, CaseInsensitiveDict] = {}
    for endpoint in schema.get_all_endpoints():
        output.setdefault(endpoint.path, CaseInsensitiveDict())
        output[endpoint.path][endpoint.method.upper()] = Bundle(endpoint.verbose_name)  # type: ignore
    return output


def make_all_rules(
    schema: "BaseOpenAPISchema", bundles: Dict[str, CaseInsensitiveDict], connections: EndpointConnections
) -> Dict[str, Rule]:
    """Create rules for all endpoints, based on the provided connections."""
    return {
        f"rule {endpoint.verbose_name}": make_rule(
            endpoint, bundles[endpoint.path][endpoint.method.upper()], connections
        )
        for endpoint in schema.get_all_endpoints()
    }


def make_rule(endpoint: "Endpoint", bundle: Bundle, connections: EndpointConnections) -> Rule:
    """Create a rule for an endpoint."""
    previous_strategies = connections.get(endpoint.verbose_name)
    if previous_strategies is not None:
        previous = _combine_strategies(previous_strategies)
    else:
        previous = none()
    return rule(target=bundle, previous=previous, case=endpoint.as_strategy())(APIStateMachine.step)  # type: ignore


def _combine_strategies(strategies: List[SearchStrategy]) -> SearchStrategy:
    """Combine a list of strategies into a single one.

    If the input is `[a, b, c]`, then the result is equivalent to `a | b | c`.
    """
    return functools.reduce(operator.or_, strategies[1:], strategies[0])
