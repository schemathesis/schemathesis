"""Open API links support.

Based on https://swagger.io/docs/specification/links/
"""
from difflib import get_close_matches
from typing import Any, Dict, Generator, List, Optional, Tuple, Union

import attr

from ...models import APIOperation, Case
from ...stateful import Direction
from ...types import NotSet
from ...utils import NOT_SET
from . import expressions
from .constants import LOCATION_TO_CONTAINER


@attr.s(slots=True, repr=False)  # pragma: no mutate
class OpenAPILink(Direction):
    """Open API link container."""

    name: str = attr.ib()  # pragma: no mutate
    status_code: str = attr.ib()  # pragma: no mutate
    definition: Dict[str, Any] = attr.ib()  # pragma: no mutate
    operation: APIOperation = attr.ib()  # pragma: no mutate
    parameters: List[Tuple[Optional[str], str, str]] = attr.ib(init=False)  # pragma: no mutate
    body: Union[Dict[str, Any], NotSet] = attr.ib(init=False)  # pragma: no mutate

    def __attrs_post_init__(self) -> None:
        self.parameters = [
            normalize_parameter(parameter, expression)
            for parameter, expression in self.definition.get("parameters", {}).items()
        ]
        self.body = self.definition.get("requestBody", NOT_SET)

    def set_data(self, case: Case, **kwargs: Any) -> None:
        """Assign all linked definitions to the new case instance."""
        context = kwargs["context"]
        self.set_parameters(case, context)
        self.set_body(case, context)
        case.set_source(context.response, context.case)

    def set_parameters(self, case: Case, context: expressions.ExpressionContext) -> None:
        for location, name, expression in self.parameters:
            container = get_container(case, location, name)
            # Might happen if there is directly specified container,
            # but the schema has no parameters of such type at all.
            # Therefore the container is empty, otherwise it will be at least an empty object
            if container is None:
                message = f"No such parameter in `{case.operation.verbose_name}`: `{name}`."
                possibilities = [param.name for param in case.operation.definition.parameters]
                matches = get_close_matches(name, possibilities)
                if matches:
                    message += f" Did you mean `{matches[0]}`?"
                raise ValueError(message)
            container[name] = expressions.evaluate(expression, context)

    def set_body(self, case: Case, context: expressions.ExpressionContext) -> None:
        if self.body is not NOT_SET:
            case.body = expressions.evaluate(self.body, context)

    def get_target_operation(self) -> APIOperation:
        if "operationId" in self.definition:
            return self.operation.schema.get_operation_by_id(self.definition["operationId"])  # type: ignore
        return self.operation.schema.get_operation_by_reference(self.definition["operationRef"])  # type: ignore


def get_container(case: Case, location: Optional[str], name: str) -> Optional[Dict[str, Any]]:
    """Get a container that suppose to store the given parameter."""
    if location:
        container_name = LOCATION_TO_CONTAINER[location]
    else:
        for param in case.operation.definition.parameters:
            if param.name == name:
                container_name = LOCATION_TO_CONTAINER[param.location]
                break
        else:
            raise ValueError(f"Parameter `{name}` is not defined in API operation `{case.operation.verbose_name}`")
    return getattr(case, container_name)


def normalize_parameter(parameter: str, expression: str) -> Tuple[Optional[str], str, str]:
    """Normalize runtime expressions.

    Runtime expressions may have parameter names prefixed with their location - `path.id`.
    At the same time, parameters could be defined without a prefix - `id`.
    We need to normalize all parameters to the same form to simplify working with them.
    """
    try:
        # The parameter name is prefixed with its location. Example: `path.id`
        location, name = tuple(parameter.split("."))
        return location, name, expression
    except ValueError:
        return None, parameter, expression


def get_all_links(operation: APIOperation) -> Generator[Tuple[str, OpenAPILink], None, None]:
    for status_code, definition in operation.definition.resolved["responses"].items():
        for name, link_definition in definition.get(operation.schema.links_field, {}).items():  # type: ignore
            yield status_code, OpenAPILink(name, status_code, link_definition, operation)


def add_link(
    responses: Dict[str, Dict[str, Any]],
    links_field: str,
    parameters: Optional[Dict[str, str]],
    request_body: Any,
    status_code: Union[str, int],
    target: Union[str, APIOperation],
) -> None:
    response = responses.setdefault(str(status_code), {})
    links_definition = response.setdefault(links_field, {})
    new_link: Dict[str, Union[str, Dict[str, str]]] = {}
    if parameters is not None:
        new_link["parameters"] = parameters
    if request_body is not None:
        new_link["requestBody"] = request_body
    if isinstance(target, str):
        name = target
        new_link["operationRef"] = target
    else:
        name = target.verbose_name
        # operationId is a dict lookup which is more efficient than using `operationRef`, since it
        # doesn't involve reference resolving when we will look up for this target during testing.
        if "operationId" in target.definition.resolved:
            new_link["operationId"] = target.definition.resolved["operationId"]
        else:
            new_link["operationRef"] = target.operation_reference
    # The name is arbitrary, so we don't really case what it is,
    # but it should not override existing links
    while name in links_definition:
        name += "_new"
    links_definition[name] = new_link
