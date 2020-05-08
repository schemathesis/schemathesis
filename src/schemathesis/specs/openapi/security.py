"""Processing of ``securityDefinitions`` or ``securitySchemes`` keywords."""
from typing import Any, Dict, List, Optional

import attr
from jsonschema import RefResolver

from ...models import Endpoint, empty_object


@attr.s(slots=True)  # pragma: no mutate
class BaseSecurityProcessor:
    def process_definitions(self, schema: Dict[str, Any], endpoint: Endpoint, resolver: RefResolver) -> None:
        """Add relevant security parameters to data generation."""
        definitions = self.get_security_definitions(schema, resolver)
        requirements = get_security_requirements(schema, endpoint)
        for name, definition in definitions.items():
            if name in requirements:
                if definition["type"] == "apiKey":
                    self.process_api_key_security_definition(definition, endpoint)
                self.process_http_security_definition(definition, endpoint)

    def get_security_definitions(self, schema: Dict[str, Any], resolver: RefResolver) -> Dict[str, Any]:
        return schema.get("securityDefinitions", {})

    def process_api_key_security_definition(self, definition: Dict[str, Any], endpoint: Endpoint) -> None:
        if definition["in"] == "query":
            endpoint.query = add_security_definition(endpoint.query, definition)
        elif definition["in"] == "header":
            endpoint.headers = add_security_definition(endpoint.headers, definition)

    def process_http_security_definition(self, definition: Dict[str, Any], endpoint: Endpoint) -> None:
        if definition["type"] == "basic":
            endpoint.headers = add_http_auth_definition(endpoint.headers)


SwaggerSecurityProcessor = BaseSecurityProcessor


@attr.s(slots=True)  # pragma: no mutate
class OpenAPISecurityProcessor(BaseSecurityProcessor):
    def get_security_definitions(self, schema: Dict[str, Any], resolver: RefResolver) -> Dict[str, Any]:
        """In Open API 3 security definitions are located in ``components`` and may have references inside."""
        components = schema.get("components", {})
        security_schemes = components.get("securitySchemes", {})
        if "$ref" in security_schemes:
            return resolver.resolve(security_schemes["$ref"])[1]
        return security_schemes

    def process_api_key_security_definition(self, definition: Dict[str, Any], endpoint: Endpoint) -> None:
        if definition["in"] == "cookie":
            endpoint.cookies = add_security_definition(endpoint.cookies, definition)
        super().process_api_key_security_definition(definition, endpoint)

    def process_http_security_definition(self, definition: Dict[str, Any], endpoint: Endpoint) -> None:
        if definition["type"] == "http":
            endpoint.headers = add_http_auth_definition(endpoint.headers, scheme=definition["scheme"].lower())


def add_security_definition(container: Optional[Dict[str, Any]], definition: Dict[str, Any]) -> Dict[str, Any]:
    """Create a JSON schema for the provided security definition."""
    name = definition["name"]
    container = container or empty_object()
    container["properties"][name] = {"name": name, "type": "string"}
    container["required"].append(name)
    return container


def add_http_auth_definition(container: Optional[Dict[str, Any]], scheme: str = "basic") -> Dict[str, Any]:
    """HTTP auth is handled via a custom `format` that is registered by Schemathesis during the import time."""
    container = container or empty_object()
    container["properties"]["Authorization"] = {"type": "string", "format": f"_{scheme}_auth"}
    container["required"].append("Authorization")
    return container


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
