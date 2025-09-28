from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Iterator, Mapping

if TYPE_CHECKING:
    from schemathesis.core.compat import RefResolver
    from schemathesis.specs.openapi.adapter.protocol import SpecificationAdapter

ORIGINAL_SECURITY_TYPE_KEY = "x-original-securuty-type"


@dataclass
class OpenApiSecurityParameters:
    """Security parameters for an API operation."""

    _parameters: list[Mapping[str, Any]]

    __slots__ = ("_parameters",)

    @classmethod
    def from_definition(
        cls,
        schema: Mapping[str, Any],
        operation: Mapping[str, Any],
        resolver: RefResolver,
        adapter: SpecificationAdapter,
    ) -> OpenApiSecurityParameters:
        return cls(list(adapter.extract_security_parameters(schema, operation, resolver)))

    def iter_parameters(self) -> Iterator[Mapping[str, Any]]:
        return iter(self._parameters)


def extract_security_parameters_v2(
    schema: Mapping[str, Any], operation: Mapping[str, Any], resolver: RefResolver
) -> Iterator[Mapping[str, Any]]:
    """Extract all required security parameters for this operation."""
    defined = extract_security_definitions_v2(schema, resolver)
    required = get_security_requirements(schema, operation)
    optional = has_optional_auth(schema, operation)

    for key in required:
        if key not in defined:
            continue
        definition = defined[key]
        ty = definition["type"]

        if ty == "apiKey":
            param = make_api_key_schema(definition, type="string")
        elif ty == "basic":
            parameter_schema = make_auth_header_schema(definition)
            param = make_auth_header(**parameter_schema)
        else:
            continue

        param[ORIGINAL_SECURITY_TYPE_KEY] = ty

        if optional:
            param = {**param, "required": False}

        yield param


def extract_security_parameters_v3(
    schema: Mapping[str, Any],
    operation: Mapping[str, Any],
    resolver: RefResolver,
) -> Iterator[Mapping[str, Any]]:
    """Extract all required security parameters for this operation."""
    defined = extract_security_definitions_v3(schema, resolver)
    required = get_security_requirements(schema, operation)
    optional = has_optional_auth(schema, operation)

    for key in required:
        if key not in defined:
            continue
        definition = defined[key]
        ty = definition["type"]

        if ty == "apiKey":
            param = make_api_key_schema(definition, schema={"type": "string"})
        elif ty == "http":
            parameter_schema = make_auth_header_schema(definition)
            param = make_auth_header(schema=parameter_schema)
        else:
            continue

        param[ORIGINAL_SECURITY_TYPE_KEY] = ty

        if optional:
            param = {**param, "required": False}

        yield param


def make_auth_header_schema(definition: dict[str, Any]) -> dict[str, str]:
    schema = definition.get("scheme", "basic").lower()
    return {"type": "string", "format": f"_{schema}_auth"}


def make_auth_header(**kwargs: Any) -> dict[str, Any]:
    return {"name": "Authorization", "in": "header", "required": True, **kwargs}


def make_api_key_schema(definition: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    return {"name": definition["name"], "required": True, "in": definition["in"], **kwargs}


def get_security_requirements(schema: Mapping[str, Any], operation: Mapping[str, Any]) -> list[str]:
    requirements = operation.get("security", schema.get("security", []))
    return [key for requirement in requirements for key in requirement]


def has_optional_auth(schema: Mapping[str, Any], operation: Mapping[str, Any]) -> bool:
    return {} in operation.get("security", schema.get("security", []))


def extract_security_definitions_v2(schema: Mapping[str, Any], resolver: RefResolver) -> Mapping[str, Any]:
    return schema.get("securityDefinitions", {})


def extract_security_definitions_v3(schema: Mapping[str, Any], resolver: RefResolver) -> Mapping[str, Any]:
    """In Open API 3 security definitions are located in ``components`` and may have references inside."""
    components = schema.get("components", {})
    security_schemes = components.get("securitySchemes", {})
    # At this point, the resolution scope could differ from the root scope, that's why we need to restore it
    # as now we resolve root-level references
    if len(resolver._scopes_stack) > 1:
        scope = resolver.resolution_scope
        resolver.pop_scope()
    else:
        scope = None
    resolve = resolver.resolve
    try:
        if "$ref" in security_schemes:
            return resolve(security_schemes["$ref"])[1]
        return {key: resolve(value["$ref"])[1] if "$ref" in value else value for key, value in security_schemes.items()}
    finally:
        if scope is not None:
            resolver._scopes_stack.append(scope)
