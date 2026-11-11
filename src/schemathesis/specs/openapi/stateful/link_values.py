from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from schemathesis.core import NOT_SET
from schemathesis.core.parameters import CONTAINER_TO_LOCATION, ContainerName, ParameterLocation
from schemathesis.core.result import Ok
from schemathesis.core.transforms import UNRESOLVABLE
from schemathesis.generation.stateful.state_machine import BASE_EXPLORATION_RATE
from schemathesis.specs.openapi.expressions import MultiMatch

if TYPE_CHECKING:
    from random import Random

    from schemathesis.generation.stateful.state_machine import Transition


@dataclass(frozen=True, slots=True)
class LinkPlan:
    """Values a link sets on the next request, chosen before the request is generated."""

    # Container name -> parameter name -> linked value
    overrides: dict[ContainerName, dict[str, Any]]
    # Linked request body sent instead of a generated one; `NOT_SET` generates one
    body: Any
    # Linked values applied on top of the request body; `NOT_SET` keeps it as is
    merged_body: Any
    applied_parameters: list[tuple[ParameterLocation, str | None]]


def count_matches(value: Any) -> int:
    if isinstance(value, MultiMatch):
        return len(value.values)
    if isinstance(value, dict):
        return max(map(count_matches, value.values()), default=1)
    if isinstance(value, list):
        return max(map(count_matches, value), default=1)
    return 1


def select_match(value: Any, index: int) -> Any:
    # Values matched from the same array stay paired by their position in it.
    if isinstance(value, MultiMatch):
        return value.values[index % len(value.values)]
    if isinstance(value, dict):
        return {key: select_match(item, index) for key, item in value.items()}
    if isinstance(value, list):
        return [select_match(item, index) for item in value]
    return value


def plan_link_values(
    transition: Transition, *, merge_body: bool, use_probability: float, calibrated: bool, random: Random
) -> LinkPlan:
    """Choose which values extracted by the link the next request uses."""
    # Picks stay out of Hypothesis's data tree: the producer's response shape can vary across runs of the same byte
    # stream when the API has mutable state, and a tracked draw would be flagged as inconsistent.
    index = _choose_match_index(transition, random)
    overrides, applied_parameters = _plan_parameters(
        transition, index, use_probability=use_probability, calibrated=calibrated, random=random
    )
    fields = overrides.pop("body", {})
    request_body = _linked_request_body(transition, index)
    if merge_body:
        body = NOT_SET
        merged_body, applied_body = _plan_merged_body(request_body, use_probability, random)
    else:
        body, applied_body = _plan_body(request_body, use_probability, random)
        merged_body = NOT_SET
    return LinkPlan(
        overrides=overrides,
        body=body,
        merged_body=with_body_fields(merged_body, fields),
        applied_parameters=applied_parameters + applied_body,
    )


def with_body_fields(body: Any, fields: dict[str, Any]) -> Any:
    """`body` with the values of `body.<name>` link parameters on top; they never stand in for the whole body."""
    if not fields:
        return body
    if body is NOT_SET:
        return fields
    # Imported on use: the stateful package imports this module while it loads
    from schemathesis.specs.openapi.stateful import merge_link_body

    return merge_link_body(body, fields)


def _choose_match_index(transition: Transition, random: Random) -> int:
    """Position of the list item that every wildcard value of this transition is taken from."""
    extracted = [param for container in transition.parameters.values() for param in container.values()]
    if transition.request_body is not None:
        extracted.append(transition.request_body)
    matches = count_matches([param.value.ok() for param in extracted if isinstance(param.value, Ok)])
    return random.randrange(matches) if matches > 1 else 0


def _linked_request_body(transition: Transition, index: int) -> Any:
    """Request body extracted by the link with wildcard values taken from the list item at `index`."""
    body = transition.request_body
    if body is None or not isinstance(body.value, Ok) or body.value.ok() is UNRESOLVABLE:
        return NOT_SET
    return select_match(body.value.ok(), index)


def _plan_parameters(
    transition: Transition, index: int, *, use_probability: float, calibrated: bool, random: Random
) -> tuple[dict[ContainerName, dict[str, Any]], list[tuple[ParameterLocation, str | None]]]:
    overrides: dict[ContainerName, dict[str, Any]] = {container: {} for container in transition.parameters}
    applied_parameters: list[tuple[ParameterLocation, str | None]] = []
    for container, name, is_required, value in _usable_parameters(transition):
        probability = _parameter_use_probability(
            container, is_required, use_probability=use_probability, calibrated=calibrated
        )
        if _biased_coin(random, probability):
            overrides[container][name] = select_match(value, index)
            applied_parameters.append((CONTAINER_TO_LOCATION[container], name))
    return overrides, applied_parameters


def _usable_parameters(transition: Transition) -> Iterator[tuple[ContainerName, str, bool, Any]]:
    """Container, name, requiredness and value of every successfully extracted parameter."""
    for container, data in transition.parameters.items():
        for name, extracted in data.items():
            if isinstance(extracted.value, Ok) and extracted.value.ok() not in (None, UNRESOLVABLE):
                yield container, name, extracted.is_required, extracted.value.ok()


def _parameter_use_probability(
    container: ContainerName, is_required: bool, *, use_probability: float, calibrated: bool
) -> float:
    if calibrated:
        return use_probability
    # No data yet: path parameters are critical for routing, required parameters should follow links more, and
    # optional parameters get more exploration.
    exploration_rate = BASE_EXPLORATION_RATE
    if container == "path_parameters":
        exploration_rate *= 0.5
    if is_required:
        exploration_rate *= 0.5
    else:
        exploration_rate *= 3.0
    return 1.0 - exploration_rate


def _plan_body(
    request_body: Any, use_probability: float, random: Random
) -> tuple[Any, list[tuple[ParameterLocation, str | None]]]:
    """Linked request body that replaces the generated one, with the body fields it sets."""
    if request_body is NOT_SET or not _biased_coin(random, use_probability):
        return NOT_SET, []
    if isinstance(request_body, dict):
        return request_body, [(ParameterLocation.BODY, field) for field in request_body]
    return request_body, [(ParameterLocation.BODY, None)]


def _plan_merged_body(
    request_body: Any, use_probability: float, random: Random
) -> tuple[Any, list[tuple[ParameterLocation, str | None]]]:
    """Linked values applied on top of the generated body, with the body fields they set."""
    if request_body is NOT_SET:
        return NOT_SET, []
    if isinstance(request_body, dict):
        selected = {
            field: value
            for field, value in request_body.items()
            if value is not UNRESOLVABLE and _biased_coin(random, use_probability)
        }
        return selected or NOT_SET, [(ParameterLocation.BODY, field) for field in selected]
    if _biased_coin(random, use_probability):
        return request_body, [(ParameterLocation.BODY, None)]
    return NOT_SET, []


def _biased_coin(random: Random, probability: float) -> bool:
    return random.random() < probability
