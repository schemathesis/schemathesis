"""Open API links support.

Based on https://swagger.io/docs/specification/links/
"""

from __future__ import annotations

from dataclasses import dataclass, field
from difflib import get_close_matches
from typing import TYPE_CHECKING, Any, Generator, Literal, TypedDict, Union, cast

from ...constants import NOT_SET
from ...models import APIOperation, Case, TransitionId
from ...stateful.state_machine import Direction
from . import expressions
from .constants import LOCATION_TO_CONTAINER
from .references import RECURSION_DEPTH_LIMIT

if TYPE_CHECKING:
    from hypothesis.vendor.pretty import RepresentationPrinter
    from jsonschema import RefResolver

    from ...types import NotSet


SCHEMATHESIS_LINK_EXTENSION = "x-schemathesis"


class SchemathesisLink(TypedDict):
    merge_body: bool


@dataclass(repr=False)
class OpenAPILink(Direction):
    """Alternative approach to link processing.

    NOTE. This class will replace `Link` in the future.
    """

    name: str
    status_code: str
    definition: dict[str, Any]
    operation: APIOperation
    parameters: list[tuple[Literal["path", "query", "header", "cookie", "body"] | None, str, str]] = field(init=False)
    body: dict[str, Any] | NotSet = field(init=False)
    merge_body: bool = True

    def __repr__(self) -> str:
        path = self.operation.path
        method = self.operation.method
        return f"state.schema['{path}']['{method}'].links['{self.status_code}']['{self.name}']"

    def _repr_pretty_(self, printer: RepresentationPrinter, cycle: bool) -> None:
        return printer.text(repr(self))

    def __post_init__(self) -> None:
        extension = self.definition.get(SCHEMATHESIS_LINK_EXTENSION)
        self.parameters = [
            normalize_parameter(parameter, expression)
            for parameter, expression in self.definition.get("parameters", {}).items()
        ]
        self.body = self.definition.get("requestBody", NOT_SET)
        if extension is not None:
            self.merge_body = extension.get("merge_body", True)

    def set_data(self, case: Case, elapsed: float, **kwargs: Any) -> None:
        """Assign all linked definitions to the new case instance."""
        context = kwargs["context"]
        overrides = self.set_parameters(case, context)
        self.set_body(case, context, overrides)
        overrides_all_parameters = True
        if case.operation.body and "body" not in overrides.get("body", []):
            overrides_all_parameters = False
        if overrides_all_parameters:
            for parameter in case.operation.iter_parameters():
                if parameter.name not in overrides.get(parameter.location, []):
                    overrides_all_parameters = False
                    break
        case.set_source(
            context.response,
            context.case,
            elapsed,
            overrides_all_parameters,
            transition_id=TransitionId(
                name=self.name,
                status_code=self.status_code,
            ),
        )

    def set_parameters(
        self, case: Case, context: expressions.ExpressionContext
    ) -> dict[Literal["path", "query", "header", "cookie", "body"], list[str]]:
        overrides: dict[Literal["path", "query", "header", "cookie", "body"], list[str]] = {}
        for location, name, expression in self.parameters:
            location, container = get_container(case, location, name)
            # Might happen if there is directly specified container,
            # but the schema has no parameters of such type at all.
            # Therefore the container is empty, otherwise it will be at least an empty object
            if container is None:
                message = f"No such parameter in `{case.operation.method.upper()} {case.operation.path}`: `{name}`."
                possibilities = [param.name for param in case.operation.iter_parameters()]
                matches = get_close_matches(name, possibilities)
                if matches:
                    message += f" Did you mean `{matches[0]}`?"
                raise ValueError(message)
            value = expressions.evaluate(expression, context)
            if value is not None:
                container[name] = value
                overrides.setdefault(location, []).append(name)
        return overrides

    def set_body(
        self,
        case: Case,
        context: expressions.ExpressionContext,
        overrides: dict[Literal["path", "query", "header", "cookie", "body"], list[str]],
    ) -> None:
        if self.body is not NOT_SET:
            evaluated = expressions.evaluate(self.body, context, evaluate_nested=True)
            overrides["body"] = ["body"]
            if self.merge_body:
                case.body = merge_body(case.body, evaluated)
            else:
                case.body = evaluated

    def get_target_operation(self) -> APIOperation:
        if "operationId" in self.definition:
            return self.operation.schema.get_operation_by_id(self.definition["operationId"])  # type: ignore
        return self.operation.schema.get_operation_by_reference(self.definition["operationRef"])  # type: ignore


def merge_body(old: Any, new: Any) -> Any:
    if isinstance(old, dict) and isinstance(new, dict):
        return {**old, **new}
    return new


def get_container(
    case: Case, location: Literal["path", "query", "header", "cookie", "body"] | None, name: str
) -> tuple[Literal["path", "query", "header", "cookie", "body"], dict[str, Any] | None]:
    """Get a container that suppose to store the given parameter."""
    if location:
        container_name = LOCATION_TO_CONTAINER[location]
    else:
        for param in case.operation.iter_parameters():
            if param.name == name:
                location = param.location
                container_name = LOCATION_TO_CONTAINER[param.location]
                break
        else:
            raise ValueError(f"Parameter `{name}` is not defined in API operation `{case.operation.verbose_name}`")
    return location, getattr(case, container_name)


def normalize_parameter(
    parameter: str, expression: str
) -> tuple[Literal["path", "query", "header", "cookie", "body"] | None, str, str]:
    """Normalize runtime expressions.

    Runtime expressions may have parameter names prefixed with their location - `path.id`.
    At the same time, parameters could be defined without a prefix - `id`.
    We need to normalize all parameters to the same form to simplify working with them.
    """
    try:
        # The parameter name is prefixed with its location. Example: `path.id`
        location, name = tuple(parameter.split("."))
        _location = cast(Literal["path", "query", "header", "cookie", "body"], location)
        return _location, name, expression
    except ValueError:
        return None, parameter, expression


def get_all_links(operation: APIOperation) -> Generator[tuple[str, OpenAPILink], None, None]:
    for status_code, definition in operation.definition.raw["responses"].items():
        definition = operation.schema.resolver.resolve_all(definition, RECURSION_DEPTH_LIMIT - 8)  # type: ignore[attr-defined]
        for name, link_definition in definition.get(operation.schema.links_field, {}).items():  # type: ignore
            yield status_code, OpenAPILink(name, status_code, link_definition, operation)


StatusCode = Union[str, int]


def _get_response_by_status_code(responses: dict[StatusCode, dict[str, Any]], status_code: str | int) -> dict:
    if isinstance(status_code, int):
        # Invalid schemas may contain status codes as integers
        if status_code in responses:
            return responses[status_code]
        # Passed here as an integer, but there is no such status code as int
        # We cast it to a string because it is either there already and we'll get relevant responses, otherwise
        # a new dict will be created because there is no such status code in the schema (as an int or a string)
        return responses.setdefault(str(status_code), {})
    if status_code.isnumeric():
        # Invalid schema but the status code is passed as a string
        numeric_status_code = int(status_code)
        if numeric_status_code in responses:
            return responses[numeric_status_code]
    # All status codes as strings, including `default` and patterned values like `5XX`
    return responses.setdefault(status_code, {})


def add_link(
    resolver: RefResolver,
    responses: dict[StatusCode, dict[str, Any]],
    links_field: str,
    parameters: dict[str, str] | None,
    request_body: Any,
    status_code: StatusCode,
    target: str | APIOperation,
    name: str | None = None,
) -> None:
    response = _get_response_by_status_code(responses, status_code)
    if "$ref" in response:
        _, response = resolver.resolve(response["$ref"])
    links_definition = response.setdefault(links_field, {})
    new_link: dict[str, str | dict[str, str]] = {}
    if parameters is not None:
        new_link["parameters"] = parameters
    if request_body is not None:
        new_link["requestBody"] = request_body
    if isinstance(target, str):
        name = name or target
        new_link["operationRef"] = target
    else:
        name = name or f"{target.method.upper()} {target.path}"
        # operationId is a dict lookup which is more efficient than using `operationRef`, since it
        # doesn't involve reference resolving when we will look up for this target during testing.
        if "operationId" in target.definition.raw:
            new_link["operationId"] = target.definition.raw["operationId"]
        else:
            new_link["operationRef"] = target.operation_reference
    # The name is arbitrary, so we don't really case what it is,
    # but it should not override existing links
    while name in links_definition:
        name += "_new"
    links_definition[name] = new_link
