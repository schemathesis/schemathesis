"""Processing of ``securityDefinitions`` or ``securitySchemes`` keywords."""
from typing import Any, Dict, Generator, List, Tuple, Type

import attr
from jsonschema import RefResolver

from ...models import Endpoint
from .parameters import OpenAPI20Parameter, OpenAPI30Parameter, OpenAPIParameter


@attr.s(slots=True)  # pragma: no mutate
class BaseSecurityProcessor:
    api_key_locations: Tuple[str, ...] = ("header", "query")
    http_security_name = "basic"
    parameter_cls: Type[OpenAPIParameter] = OpenAPI20Parameter

    def process_definitions(self, schema: Dict[str, Any], endpoint: Endpoint, resolver: RefResolver) -> None:
        """Add relevant security parameters to data generation."""
        for definition in self._get_active_definitions(schema, endpoint, resolver):
            if definition["type"] == "apiKey":
                self.process_api_key_security_definition(definition, endpoint)
            self.process_http_security_definition(definition, endpoint)

    def _get_active_definitions(
        self, schema: Dict[str, Any], endpoint: Endpoint, resolver: RefResolver
    ) -> Generator[Dict[str, Any], None, None]:
        """Get only security definitions active for the given endpoint."""
        definitions = self.get_security_definitions(schema, resolver)
        requirements = get_security_requirements(schema, endpoint)
        for name, definition in definitions.items():
            if name in requirements:
                yield definition

    def get_security_definitions(self, schema: Dict[str, Any], resolver: RefResolver) -> Dict[str, Any]:
        return schema.get("securityDefinitions", {})

    def get_security_definitions_as_parameters(
        self, schema: Dict[str, Any], endpoint: Endpoint, resolver: RefResolver, location: str
    ) -> List[Dict[str, Any]]:
        """Security definitions converted to OAS parameters.

        We need it to get proper serialization that will be applied on generated values. For this case it is only
        coercing to a string.
        """
        return [
            self._to_parameter(definition)
            for definition in self._get_active_definitions(schema, endpoint, resolver)
            if self._is_match(definition, location)
        ]

    def process_api_key_security_definition(self, definition: Dict[str, Any], endpoint: Endpoint) -> None:
        parameter = self.parameter_cls(self._make_api_key_parameter(definition))
        endpoint.add_parameter(parameter)

    def process_http_security_definition(self, definition: Dict[str, Any], endpoint: Endpoint) -> None:
        if definition["type"] == self.http_security_name:
            parameter = self.parameter_cls(self._make_http_auth_parameter(definition))
            endpoint.add_parameter(parameter)

    def _is_match(self, definition: Dict[str, Any], location: str) -> bool:
        return (definition["type"] == "apiKey" and location in self.api_key_locations) or (
            definition["type"] == self.http_security_name and location == "header"
        )

    def _to_parameter(self, definition: Dict[str, Any]) -> Dict[str, Any]:
        func = {
            "apiKey": self._make_api_key_parameter,
            self.http_security_name: self._make_http_auth_parameter,
        }[definition["type"]]
        return func(definition)

    def _make_http_auth_parameter(self, definition: Dict[str, Any]) -> Dict[str, Any]:
        scheme = definition.get("scheme", "basic").lower()
        # TODO. reduce duplication
        return {
            "name": "Authorization",
            "in": "header",
            "required": True,
            "type": "string",
            "format": f"_{scheme}_auth",
        }

    def _make_api_key_parameter(self, definition: Dict[str, Any]) -> Dict[str, Any]:
        return {"type": "string", "name": definition["name"], "required": True, "in": definition["in"]}


SwaggerSecurityProcessor = BaseSecurityProcessor


@attr.s(slots=True)  # pragma: no mutate
class OpenAPISecurityProcessor(BaseSecurityProcessor):
    api_key_locations = ("header", "cookie", "query")
    http_security_name = "http"
    parameter_cls: Type[OpenAPIParameter] = OpenAPI30Parameter

    def get_security_definitions(self, schema: Dict[str, Any], resolver: RefResolver) -> Dict[str, Any]:
        """In Open API 3 security definitions are located in ``components`` and may have references inside."""
        components = schema.get("components", {})
        security_schemes = components.get("securitySchemes", {})
        if "$ref" in security_schemes:
            return resolver.resolve(security_schemes["$ref"])[1]
        return security_schemes

    def _make_http_auth_parameter(self, definition: Dict[str, Any]) -> Dict[str, Any]:
        scheme = definition.get("scheme", "basic").lower()
        return {
            "name": "Authorization",
            "in": "header",
            "required": True,
            "schema": {"type": "string", "format": f"_{scheme}_auth"},
        }

    def _make_api_key_parameter(self, definition: Dict[str, Any]) -> Dict[str, Any]:
        return {"name": definition["name"], "in": definition["in"], "required": True, "schema": {"type": "string"}}


def get_security_requirements(schema: Dict[str, Any], endpoint: Endpoint) -> List[str]:
    """Get applied security requirements for the given endpoint."""
    # https://github.com/OAI/OpenAPI-Specification/blob/master/versions/2.0.md#operation-object
    # > This definition overrides any declared top-level security.
    # > To remove a top-level security declaration, an empty array can be used.
    global_requirements = schema.get("security", [])
    local_requirements = endpoint.definition.raw.get("security", None)
    if local_requirements is not None:
        requirements = local_requirements
    else:
        requirements = global_requirements
    return [key for requirement in requirements for key in requirement]
