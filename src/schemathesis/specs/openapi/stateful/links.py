from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from schemathesis.core import NOT_SET, NotSet
from schemathesis.core.errors import InvalidTransition, OperationNotFound, TransitionValidationError, format_transition
from schemathesis.core.parameters import ParameterLocation
from schemathesis.core.result import Err, Ok, Result
from schemathesis.generation.stateful.state_machine import ExtractedParam, StepOutput, Transition
from schemathesis.schemas import APIOperation
from schemathesis.specs.openapi import expressions

SCHEMATHESIS_LINK_EXTENSION = "x-schemathesis"


@dataclass
class NormalizedParameter:
    """Processed link parameter with resolved container information."""

    location: ParameterLocation | None
    name: str
    expression: str
    container_name: str
    is_required: bool

    __slots__ = ("location", "name", "expression", "container_name", "is_required")


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
    is_inferred: bool

    __slots__ = (
        "name",
        "status_code",
        "source",
        "target",
        "parameters",
        "body",
        "merge_body",
        "is_inferred",
        "_cached_extract",
    )

    def __init__(self, name: str, status_code: str, definition: dict[str, Any], source: APIOperation):
        from schemathesis.specs.openapi.schemas import OpenApiSchema

        self.name = name
        self.status_code = status_code
        self.source = source
        assert isinstance(source.schema, OpenApiSchema)
        errors = []

        get_operation: Callable[[str], APIOperation]
        if "operationId" in definition:
            operation_reference = definition["operationId"]
            get_operation = source.schema.find_operation_by_id
        else:
            operation_reference = definition["operationRef"]
            get_operation = source.schema.find_operation_by_reference

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
        self.is_inferred = extension.get("is_inferred", False) if extension else False

        if errors:
            raise InvalidTransition(
                name=self.name,
                source=self.source.label,
                target=target,
                status_code=self.status_code,
                errors=errors,
            )

        self._cached_extract = lru_cache(8)(self._extract_impl)

    @property
    def full_name(self) -> str:
        return format_transition(self.source.label, self.status_code, self.name, self.target.label)

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
                location = ParameterLocation(_location)
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

            is_required = False
            if hasattr(self, "target"):
                try:
                    container_name = self._get_parameter_container(location, name)
                except TransitionValidationError as exc:
                    errors.append(exc)
                    continue

                for param in self.target.iter_parameters():
                    if param.name == name:
                        is_required = param.is_required
                        break
            else:
                continue
            result.append(NormalizedParameter(location, name, expression, container_name, is_required=is_required))
        return result

    def _get_parameter_container(self, location: ParameterLocation | None, name: str) -> str:
        """Resolve parameter container either from explicit location or by looking up in target operation."""
        if location:
            return location.container_name

        for param in self.target.iter_parameters():
            if param.name == name:
                return param.location.container_name
        raise TransitionValidationError(f"Parameter `{name}` is not defined in API operation `{self.target.label}`")

    def extract(self, output: StepOutput) -> Transition:
        return self._cached_extract(StepOutputWrapper(output))

    def _extract_impl(self, wrapper: StepOutputWrapper) -> Transition:
        output = wrapper.output
        return Transition(
            id=self.full_name,
            parent_id=output.case.id,
            is_inferred=self.is_inferred,
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
            container[parameter.name] = ExtractedParam(
                definition=parameter.expression, value=value, is_required=parameter.is_required
            )
        return extracted

    def extract_body(self, output: StepOutput) -> ExtractedParam | None:
        if not isinstance(self.body, NotSet):
            value: Result[Any, Exception]
            try:
                value = Ok(expressions.evaluate(self.body, output, evaluate_nested=True))
            except Exception as exc:
                value = Err(exc)
            return ExtractedParam(definition=self.body, value=value, is_required=True)
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
