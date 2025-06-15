from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Callable, Generator, Literal, cast

from schemathesis.core import NOT_SET, NotSet
from schemathesis.core.errors import InvalidTransition, OperationNotFound, TransitionValidationError
from schemathesis.core.result import Err, Ok, Result
from schemathesis.generation.stateful.state_machine import ExtractedParam, StepOutput, Transition
from schemathesis.schemas import APIOperation
from schemathesis.specs.openapi import expressions
from schemathesis.specs.openapi.constants import LOCATION_TO_CONTAINER
from schemathesis.specs.openapi.references import RECURSION_DEPTH_LIMIT

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
        from schemathesis.specs.openapi.schemas import BaseOpenAPISchema

        self.name = name
        self.status_code = status_code
        self.source = source
        assert isinstance(source.schema, BaseOpenAPISchema)
        errors = []

        get_operation: Callable[[str], APIOperation]
        if "operationId" in definition:
            operation_reference = definition["operationId"]
            get_operation = source.schema.get_operation_by_id
        else:
            operation_reference = definition["operationRef"]
            get_operation = source.schema.get_operation_by_reference

        try:
            self.target = get_operation(operation_reference)
            target = self.target.label
        except OperationNotFound:
            target = operation_reference
            errors.append(TransitionValidationError(f"Operation '{operation_reference}' not found"))

        extension = definition.get(SCHEMATHESIS_LINK_EXTENSION)
        self.parameters = self._normalize_parameters(definition.get("parameters", {}), errors)
        self.body = definition.get("requestBody", NOT_SET)
        self.merge_body = extension.get("merge_body", True) if extension else True

        if errors:
            raise InvalidTransition(
                name=self.name,
                source=self.source.label,
                target=target,
                status_code=self.status_code,
                errors=errors,
            )

        self._cached_extract = lru_cache(8)(self._extract_impl)

    def _normalize_parameters(
        self, parameters: dict[str, str], errors: list[TransitionValidationError]
    ) -> list[NormalizedParameter]:
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

            if isinstance(expression, str):
                try:
                    parsed = expressions.parser.parse(expression)
                    # Find NonBodyRequest nodes that reference source parameters
                    for node in parsed:
                        if isinstance(node, expressions.nodes.NonBodyRequest):
                            # Check if parameter exists in source operation
                            if not any(
                                p.name == node.parameter and p.location == node.location
                                for p in self.source.iter_parameters()
                            ):
                                errors.append(
                                    TransitionValidationError(
                                        f"Expression `{expression}` references non-existent {node.location} parameter "
                                        f"`{node.parameter}` in `{self.source.label}`"
                                    )
                                )
                except Exception as exc:
                    errors.append(TransitionValidationError(str(exc)))

            if hasattr(self, "target"):
                try:
                    container_name = self._get_parameter_container(location, name)
                except TransitionValidationError as exc:
                    errors.append(exc)
                    continue
            else:
                continue
            result.append(NormalizedParameter(location, name, expression, container_name))
        return result

    def _get_parameter_container(self, location: ParameterLocation | None, name: str) -> str:
        """Resolve parameter container either from explicit location or by looking up in target operation."""
        if location:
            return LOCATION_TO_CONTAINER[location]

        for param in self.target.iter_parameters():
            if param.name == name:
                return LOCATION_TO_CONTAINER[param.location]
        raise TransitionValidationError(f"Parameter `{name}` is not defined in API operation `{self.target.label}`")

    def extract(self, output: StepOutput) -> Transition:
        return self._cached_extract(StepOutputWrapper(output))

    def _extract_impl(self, wrapper: StepOutputWrapper) -> Transition:
        output = wrapper.output
        return Transition(
            id=f"{self.source.label} -> [{self.status_code}] {self.name} -> {self.target.label}",
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


def get_all_links(
    operation: APIOperation,
) -> Generator[tuple[str, Result[OpenApiLink, InvalidTransition]], None, None]:
    for status_code, definition in operation.definition.raw["responses"].items():
        definition = operation.schema.resolver.resolve_all(definition, RECURSION_DEPTH_LIMIT - 8)  # type: ignore[attr-defined]
        for name, link_definition in definition.get(operation.schema.links_field, {}).items():  # type: ignore
            try:
                link = OpenApiLink(name, status_code, link_definition, operation)
                yield status_code, Ok(link)
            except InvalidTransition as exc:
                yield status_code, Err(exc)
