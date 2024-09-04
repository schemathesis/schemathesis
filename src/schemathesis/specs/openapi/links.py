"""Open API links support.

Based on https://swagger.io/docs/specification/links/
"""

from __future__ import annotations

from dataclasses import dataclass, field
from difflib import get_close_matches
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, Generator, Literal, NoReturn, Sequence, TypedDict, Union, cast

from jsonschema import RefResolver

from ...constants import NOT_SET
from ...internal.copy import fast_deepcopy
from ...models import APIOperation, Case, TransitionId
from ...parameters import ParameterSet
from ...stateful import ParsedData, StatefulTest, UnresolvableLink
from ...stateful.state_machine import Direction
from ...types import NotSet
from . import expressions
from .constants import LOCATION_TO_CONTAINER
from .parameters import OpenAPI20Body, OpenAPI30Body, OpenAPIParameter
from .references import RECURSION_DEPTH_LIMIT, Unresolvable

if TYPE_CHECKING:
    from ...transports.responses import GenericResponse


@dataclass(repr=False)
class Link(StatefulTest):
    operation: APIOperation
    parameters: dict[str, Any]
    request_body: Any = NOT_SET
    merge_body: bool = True

    def __post_init__(self) -> None:
        if self.request_body is not NOT_SET and not self.operation.body:
            # Link defines `requestBody` for a parameter that does not accept one
            raise ValueError(
                f"Request body is not defined in API operation {self.operation.method.upper()} {self.operation.path}"
            )

    @classmethod
    def from_definition(cls, name: str, definition: dict[str, dict[str, Any]], source_operation: APIOperation) -> Link:
        # Links can be behind a reference
        _, definition = source_operation.schema.resolver.resolve_in_scope(  # type: ignore
            definition, source_operation.definition.scope
        )
        if "operationId" in definition:
            # source_operation.schema is `BaseOpenAPISchema` and has this method
            operation = source_operation.schema.get_operation_by_id(definition["operationId"])  # type: ignore
        else:
            operation = source_operation.schema.get_operation_by_reference(definition["operationRef"])  # type: ignore
        extension = definition.get(SCHEMATHESIS_LINK_EXTENSION)
        return cls(
            # Pylint can't detect that the API operation is always defined at this point
            # E.g. if there is no matching operation or no operations at all, then a ValueError will be risen
            name=name,
            operation=operation,
            parameters=definition.get("parameters", {}),
            request_body=definition.get("requestBody", NOT_SET),  # `None` might be a valid value - `null`
            merge_body=extension.get("merge_body", True) if extension is not None else True,
        )

    def parse(self, case: Case, response: GenericResponse) -> ParsedData:
        """Parse data into a structure expected by links definition."""
        context = expressions.ExpressionContext(case=case, response=response)
        parameters = {}
        for parameter, expression in self.parameters.items():
            evaluated = expressions.evaluate(expression, context)
            if isinstance(evaluated, Unresolvable):
                raise UnresolvableLink(f"Unresolvable reference in the link: {expression}")
            parameters[parameter] = evaluated
        body = expressions.evaluate(self.request_body, context, evaluate_nested=True)
        if self.merge_body:
            body = merge_body(case.body, body)
        return ParsedData(parameters=parameters, body=body)

    def is_match(self) -> bool:
        return self.operation.schema.filter_set.match(SimpleNamespace(operation=self.operation))

    def make_operation(self, collected: list[ParsedData]) -> APIOperation:
        """Create a modified version of the original API operation with additional data merged in."""
        # We split the gathered data among all locations & store the original parameter
        containers = {
            location: {
                parameter.name: {"options": [], "parameter": parameter}
                for parameter in getattr(self.operation, container_name)
            }
            for location, container_name in LOCATION_TO_CONTAINER.items()
        }
        # There might be duplicates in the data
        for item in set(collected):
            for name, value in item.parameters.items():
                container = self._get_container_by_parameter_name(name, containers)
                container.append(value)
            if "body" in containers["body"] and item.body is not NOT_SET:
                containers["body"]["body"]["options"].append(item.body)
        # These are the final `path_parameters`, `query`, and other API operation components
        components: dict[str, ParameterSet] = {
            container_name: getattr(self.operation, container_name).__class__()
            for location, container_name in LOCATION_TO_CONTAINER.items()
        }
        # Here are all components that are filled with parameters
        for location, parameters in containers.items():
            for parameter_data in parameters.values():
                parameter = parameter_data["parameter"]
                if parameter_data["options"]:
                    definition = fast_deepcopy(parameter.definition)
                    if "schema" in definition:
                        # The actual schema doesn't matter since we have a list of allowed values
                        definition["schema"] = {"enum": parameter_data["options"]}
                    else:
                        # Other schema-related keywords will be ignored later, during the canonicalisation step
                        # inside `hypothesis-jsonschema`
                        definition["enum"] = parameter_data["options"]
                    new_parameter: OpenAPIParameter
                    if isinstance(parameter, OpenAPI30Body):
                        new_parameter = parameter.__class__(
                            definition, media_type=parameter.media_type, required=parameter.required
                        )
                    elif isinstance(parameter, OpenAPI20Body):
                        new_parameter = parameter.__class__(definition, media_type=parameter.media_type)
                    else:
                        new_parameter = parameter.__class__(definition)
                    components[LOCATION_TO_CONTAINER[location]].add(new_parameter)
                else:
                    # No options were gathered for this parameter - use the original one
                    components[LOCATION_TO_CONTAINER[location]].add(parameter)
        return self.operation.clone(**components)

    def _get_container_by_parameter_name(self, full_name: str, templates: dict[str, dict[str, dict[str, Any]]]) -> list:
        """Detect in what request part the parameters is defined."""
        location: str | None
        try:
            # The parameter name is prefixed with its location. Example: `path.id`
            location, name = full_name.split(".")
        except ValueError:
            location, name = None, full_name
        if location:
            try:
                parameters = templates[location]
            except KeyError:
                self._unknown_parameter(full_name)
        else:
            for parameters in templates.values():
                if name in parameters:
                    break
            else:
                self._unknown_parameter(full_name)
        if not parameters:
            self._unknown_parameter(full_name)
        return parameters[name]["options"]

    def _unknown_parameter(self, name: str) -> NoReturn:
        raise ValueError(
            f"Parameter `{name}` is not defined in API operation {self.operation.method.upper()} {self.operation.path}"
        )


def get_links(response: GenericResponse, operation: APIOperation, field: str) -> Sequence[Link]:
    """Get `x-links` / `links` definitions from the schema."""
    responses = operation.definition.raw["responses"]
    if str(response.status_code) in responses:
        definition = responses[str(response.status_code)]
    elif response.status_code in responses:
        definition = responses[response.status_code]
    else:
        definition = responses.get("default", {})
    if not definition:
        return []
    _, definition = operation.schema.resolver.resolve_in_scope(definition, operation.definition.scope)  # type: ignore[attr-defined]
    links = definition.get(field, {})
    return [Link.from_definition(name, definition, operation) for name, definition in links.items()]


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
