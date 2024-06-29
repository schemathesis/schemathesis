"""Processing of ``securityDefinitions`` or ``securitySchemes`` keywords."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar, Generator

from jsonschema import RefResolver

from ...models import APIOperation
from .parameters import OpenAPI20Parameter, OpenAPI30Parameter, OpenAPIParameter


@dataclass
class BaseSecurityProcessor:
    api_key_locations: ClassVar[tuple[str, ...]] = ("header", "query")
    http_security_name: ClassVar[str] = "basic"
    parameter_cls: ClassVar[type[OpenAPIParameter]] = OpenAPI20Parameter

    def process_definitions(self, schema: dict[str, Any], operation: APIOperation, resolver: RefResolver) -> None:
        """Add relevant security parameters to data generation."""
        __tracebackhide__ = True
        for definition in self._get_active_definitions(schema, operation, resolver):
            name = definition.get("name")
            location = definition.get("in")
            if name is not None and location is not None and operation.get_parameter(name, location) is not None:
                # Such parameter is already defined
                continue
            if definition["type"] == "apiKey":
                self.process_api_key_security_definition(definition, operation)
            self.process_http_security_definition(definition, operation)

    @staticmethod
    def get_security_requirements(schema: dict[str, Any], operation: APIOperation) -> list[str]:
        """Get applied security requirements for the given API operation."""
        # https://github.com/OAI/OpenAPI-Specification/blob/master/versions/2.0.md#operation-object
        # > This definition overrides any declared top-level security.
        # > To remove a top-level security declaration, an empty array can be used.
        global_requirements = schema.get("security", [])
        local_requirements = operation.definition.raw.get("security", None)
        if local_requirements is not None:
            requirements = local_requirements
        else:
            requirements = global_requirements
        return [key for requirement in requirements for key in requirement]

    def _get_active_definitions(
        self, schema: dict[str, Any], operation: APIOperation, resolver: RefResolver
    ) -> Generator[dict[str, Any], None, None]:
        """Get only security definitions active for the given API operation."""
        definitions = self.get_security_definitions(schema, resolver)
        requirements = self.get_security_requirements(schema, operation)
        for name, definition in definitions.items():
            if name in requirements:
                yield definition

    def get_security_definitions(self, schema: dict[str, Any], resolver: RefResolver) -> dict[str, Any]:
        return schema.get("securityDefinitions", {})

    def get_security_definitions_as_parameters(
        self, schema: dict[str, Any], operation: APIOperation, resolver: RefResolver, location: str
    ) -> list[dict[str, Any]]:
        """Security definitions converted to OAS parameters.

        We need it to get proper serialization that will be applied on generated values. For this case it is only
        coercing to a string.
        """
        return [
            self._to_parameter(definition)
            for definition in self._get_active_definitions(schema, operation, resolver)
            if self._is_match(definition, location)
        ]

    def process_api_key_security_definition(self, definition: dict[str, Any], operation: APIOperation) -> None:
        parameter = self.parameter_cls(self._make_api_key_parameter(definition))
        operation.add_parameter(parameter)

    def process_http_security_definition(self, definition: dict[str, Any], operation: APIOperation) -> None:
        if definition["type"] == self.http_security_name:
            parameter = self.parameter_cls(self._make_http_auth_parameter(definition))
            operation.add_parameter(parameter)

    def _is_match(self, definition: dict[str, Any], location: str) -> bool:
        return (definition["type"] == "apiKey" and location in self.api_key_locations) or (
            definition["type"] == self.http_security_name and location == "header"
        )

    def _to_parameter(self, definition: dict[str, Any]) -> dict[str, Any]:
        func = {
            "apiKey": self._make_api_key_parameter,
            self.http_security_name: self._make_http_auth_parameter,
        }[definition["type"]]
        return func(definition)

    def _make_http_auth_parameter(self, definition: dict[str, Any]) -> dict[str, Any]:
        schema = make_auth_header_schema(definition)
        return make_auth_header(**schema)

    def _make_api_key_parameter(self, definition: dict[str, Any]) -> dict[str, Any]:
        return make_api_key_schema(definition, type="string")


def make_auth_header_schema(definition: dict[str, Any]) -> dict[str, str]:
    schema = definition.get("scheme", "basic").lower()
    return {"type": "string", "format": f"_{schema}_auth"}


def make_auth_header(**kwargs: Any) -> dict[str, Any]:
    return {"name": "Authorization", "in": "header", "required": True, **kwargs}


def make_api_key_schema(definition: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    return {"name": definition["name"], "required": True, "in": definition["in"], **kwargs}


SwaggerSecurityProcessor = BaseSecurityProcessor


@dataclass
class OpenAPISecurityProcessor(BaseSecurityProcessor):
    api_key_locations: ClassVar[tuple[str, ...]] = ("header", "cookie", "query")
    http_security_name: ClassVar[str] = "http"
    parameter_cls: ClassVar[type[OpenAPIParameter]] = OpenAPI30Parameter

    def get_security_definitions(self, schema: dict[str, Any], resolver: RefResolver) -> dict[str, Any]:
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
            return {
                key: resolve(value["$ref"])[1] if "$ref" in value else value for key, value in security_schemes.items()
            }
        finally:
            if scope is not None:
                resolver._scopes_stack.append(scope)

    def _make_http_auth_parameter(self, definition: dict[str, Any]) -> dict[str, Any]:
        schema = make_auth_header_schema(definition)
        return make_auth_header(schema=schema)

    def _make_api_key_parameter(self, definition: dict[str, Any]) -> dict[str, Any]:
        return make_api_key_schema(definition, schema={"type": "string"})
