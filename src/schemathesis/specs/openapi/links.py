from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import TYPE_CHECKING, Any, Generator, Literal, Union, cast

from schemathesis.core import NOT_SET, NotSet
from schemathesis.core.result import Err, Ok, Result
from schemathesis.generation.stateful.state_machine import ExtractedParam, StepOutput, Transition
from schemathesis.schemas import APIOperation

from . import expressions
from .constants import LOCATION_TO_CONTAINER
from .references import RECURSION_DEPTH_LIMIT

if TYPE_CHECKING:
    from jsonschema import RefResolver


SCHEMATHESIS_LINK_EXTENSION = "x-schemathesis"
ParameterLocation = Literal["path", "query", "header", "cookie", "body"]


@dataclass
class NormalizedParameter:
    """Processed link parameter with resolved container information."""

    location: ParameterLocation | None
    name: str
    expression: str
    container_name: str

    __slots__ = ("location", "name", "expression", "container_name")


@dataclass(repr=False)
class OpenApiLink:
    """Represents an OpenAPI link between operations."""

    name: str
    status_code: str
    source: APIOperation
    target: APIOperation
    parameters: list[NormalizedParameter]
    body: dict[str, Any] | NotSet
    merge_body: bool

    __slots__ = ("name", "status_code", "source", "target", "parameters", "body", "merge_body", "_cached_extract")

    def __init__(self, name: str, status_code: str, definition: dict[str, Any], source: APIOperation):
        self.name = name
        self.status_code = status_code
        self.source = source

        if "operationId" in definition:
            self.target = source.schema.get_operation_by_id(definition["operationId"])  # type: ignore
        else:
            self.target = source.schema.get_operation_by_reference(definition["operationRef"])  # type: ignore

        extension = definition.get(SCHEMATHESIS_LINK_EXTENSION)
        self.parameters = self._normalize_parameters(definition.get("parameters", {}))
        self.body = definition.get("requestBody", NOT_SET)
        self.merge_body = extension.get("merge_body", True) if extension else True

        self._cached_extract = lru_cache(8)(self._extract_impl)

    def _normalize_parameters(self, parameters: dict[str, str]) -> list[NormalizedParameter]:
        """Process link parameters and resolve their container locations.

        Handles both explicit locations (e.g., "path.id") and implicit ones resolved from target operation.
        """
        result = []
        for parameter, expression in parameters.items():
            location: ParameterLocation | None
            try:
                # The parameter name is prefixed with its location. Example: `path.id`
                _location, name = tuple(parameter.split("."))
                location = cast(ParameterLocation, _location)
            except ValueError:
                location = None
                name = parameter

            container_name = self._get_parameter_container(location, name)
            result.append(NormalizedParameter(location, name, expression, container_name))
        return result

    def _get_parameter_container(self, location: ParameterLocation | None, name: str) -> str:
        """Resolve parameter container either from explicit location or by looking up in target operation."""
        if location:
            return LOCATION_TO_CONTAINER[location]

        for param in self.target.iter_parameters():
            if param.name == name:
                return LOCATION_TO_CONTAINER[param.location]
        raise ValueError(f"Parameter `{name}` is not defined in API operation `{self.target.label}`")

    def extract(self, output: StepOutput) -> Transition:
        return self._cached_extract(StepOutputWrapper(output))

    def _extract_impl(self, wrapper: StepOutputWrapper) -> Transition:
        output = wrapper.output
        return Transition(
            id=f"{self.source.label} - {self.status_code} - {self.name}",
            parent_id=output.case.id,
            parameters=self.extract_parameters(output),
            request_body=self.extract_body(output),
        )

    def extract_parameters(self, output: StepOutput) -> dict[str, dict[str, ExtractedParam]]:
        """Extract parameters using runtime expressions.

        Returns a two-level dictionary: container -> parameter name -> extracted value
        """
        extracted: dict[str, dict[str, ExtractedParam]] = {}
        for parameter in self.parameters:
            container = extracted.setdefault(parameter.container_name, {})
            value: Result[Any, Exception]
            try:
                value = Ok(expressions.evaluate(parameter.expression, output))
            except Exception as exc:
                value = Err(exc)
            container[parameter.name] = ExtractedParam(definition=parameter.expression, value=value)
        return extracted

    def extract_body(self, output: StepOutput) -> ExtractedParam | None:
        if not isinstance(self.body, NotSet):
            value: Result[Any, Exception]
            try:
                value = Ok(expressions.evaluate(self.body, output, evaluate_nested=True))
            except Exception as exc:
                value = Err(exc)
            return ExtractedParam(definition=self.body, value=value)
        return None


@dataclass
class StepOutputWrapper:
    """Wrapper for StepOutput that uses only case_id for hash-based caching."""

    output: StepOutput
    __slots__ = ("output",)

    def __hash__(self) -> int:
        return hash(self.output.case.id)

    def __eq__(self, other: object) -> bool:
        assert isinstance(other, StepOutputWrapper)
        return self.output.case.id == other.output.case.id


def get_all_links(operation: APIOperation) -> Generator[tuple[str, OpenApiLink], None, None]:
    for status_code, definition in operation.definition.raw["responses"].items():
        definition = operation.schema.resolver.resolve_all(definition, RECURSION_DEPTH_LIMIT - 8)  # type: ignore[attr-defined]
        for name, link_definition in definition.get(operation.schema.links_field, {}).items():  # type: ignore
            yield status_code, OpenApiLink(name, status_code, link_definition, operation)


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
