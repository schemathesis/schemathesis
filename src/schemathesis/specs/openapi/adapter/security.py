from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from schemathesis.config import ApiKeyAuthConfig, HttpBasicAuthConfig, HttpBearerAuthConfig
from schemathesis.core.parameters import ParameterLocation
from schemathesis.generation.meta import CoveragePhaseData, FuzzingPhaseData, StatefulPhaseData
from schemathesis.specs.openapi.auths import ApiKeyAuthProvider, HttpBasicAuthProvider, HttpBearerAuthProvider

if TYPE_CHECKING:
    from schemathesis.auths import AuthContext, AuthProvider
    from schemathesis.core.compat import RefResolver
    from schemathesis.generation.case import Case
    from schemathesis.specs.openapi.adapter.protocol import SpecificationAdapter

ORIGINAL_SECURITY_TYPE_KEY = "x-original-security-type"


def _matches_security_parameter(
    definition: Mapping[str, Any],
    param_name: str,
    param_location: ParameterLocation,
) -> bool:
    """Check if security definition would set the given parameter."""
    ty = definition.get("type")

    if ty == "http":
        return param_name == "Authorization" and param_location == ParameterLocation.HEADER

    if ty == "apiKey":
        location_map = {
            "header": ParameterLocation.HEADER,
            "query": ParameterLocation.QUERY,
            "cookie": ParameterLocation.COOKIE,
        }
        loc = definition.get("in")
        return isinstance(loc, str) and param_name == definition.get("name") and param_location == location_map.get(loc)

    return False


class OpenApiSecurity:
    """OpenAPI security scheme definitions and authentication logic."""

    raw_schema: Mapping[str, Any]
    adapter: SpecificationAdapter
    resolver: RefResolver
    _auth_provider_cache: dict[str, AuthProvider]
    _resolved_definitions: Mapping[str, Mapping[str, Any]] | None

    __slots__ = ("raw_schema", "adapter", "resolver", "_auth_provider_cache", "_resolved_definitions")

    def __init__(self, raw_schema: Mapping[str, Any], adapter: SpecificationAdapter, resolver: RefResolver) -> None:
        self.raw_schema = raw_schema
        self.adapter = adapter
        self.resolver = resolver
        self._auth_provider_cache = {}
        self._resolved_definitions = None

    @property
    def security_definitions(self) -> Mapping[str, Mapping[str, Any]]:
        """Get security scheme definitions from the schema."""
        if self._resolved_definitions is None:
            self._resolved_definitions = self.adapter.extract_security_definitions(self.raw_schema, self.resolver)
        return self._resolved_definitions

    def auth_provider_for(
        self, scheme: str, config: ApiKeyAuthConfig | HttpBasicAuthConfig | HttpBearerAuthConfig
    ) -> AuthProvider:
        """Get or build an auth provider for the given scheme (cached per scheme).

        Args:
            scheme: Name of the security scheme
            config: Auth configuration

        """
        if scheme not in self._auth_provider_cache:
            definition = self.security_definitions[scheme]
            self._auth_provider_cache[scheme] = build_auth_provider(config, definition)
        return self._auth_provider_cache[scheme]

    def apply_auth(
        self,
        case: Case,
        context: AuthContext,
        configured_schemes: Mapping[str, ApiKeyAuthConfig | HttpBasicAuthConfig | HttpBearerAuthConfig],
    ) -> bool:
        """Apply OpenAPI-aware authentication to a test case.

        Args:
            case: Test case to authenticate
            context: Auth context
            configured_schemes: Dict of configured auth schemes from config

        """
        # Check if a security parameter was intentionally removed during negative testing
        meta = case.meta
        if meta and meta.generation.mode.is_negative:
            phase_data = meta.phase.data
            if isinstance(phase_data, (FuzzingPhaseData, CoveragePhaseData, StatefulPhaseData)):
                mutated_param = phase_data.parameter
                mutated_location = phase_data.parameter_location
                if mutated_param and mutated_location:
                    # Check if any security scheme would set this parameter
                    security_definitions = self.security_definitions
                    for definition in security_definitions.values():
                        if _matches_security_parameter(definition, mutated_param, mutated_location):
                            # Don't re-apply auth that was intentionally removed for testing
                            return False

        # Get security requirements for this operation
        operation_definition = case.operation.definition.raw

        # Security requirements: OR semantics (first match wins), AND semantics (all in requirement)
        security_requirements = operation_definition.get("security", self.raw_schema.get("security", []))

        if not security_requirements:
            return False

        security_definitions = self.security_definitions

        # Try each security requirement (OR semantics)
        for requirement in security_requirements:
            if not isinstance(requirement, dict):
                continue
            # Check if all schemes in this requirement can be satisfied (AND semantics)
            providers_to_apply = []
            can_satisfy = True

            for scheme_name in requirement:
                if scheme_name not in configured_schemes or scheme_name not in security_definitions:
                    can_satisfy = False
                    break

                config = configured_schemes[scheme_name]
                provider = self.auth_provider_for(scheme_name, config)
                providers_to_apply.append(provider)

            # If we can satisfy this requirement, apply all providers
            if can_satisfy and providers_to_apply:
                for provider in providers_to_apply:
                    data = provider.get(case, context)
                    assert data is not None
                    provider.set(case, data, context)
                case._has_explicit_auth = True
                return True

        return False


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
    """Build schema dict for Authorization header based on auth scheme."""
    schema = definition.get("scheme", "basic").lower()
    return {"type": "string", "format": f"_{schema}_auth"}


def make_auth_header(**kwargs: Any) -> dict[str, Any]:
    """Build Authorization header security parameter."""
    return {"name": "Authorization", "in": "header", "required": True, **kwargs}


def make_api_key_schema(definition: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    """Build API key security parameter from security definition."""
    return {"name": definition["name"], "required": True, "in": definition["in"], **kwargs}


def get_security_requirements(schema: Mapping[str, Any], operation: Mapping[str, Any]) -> list[str]:
    requirements = operation.get("security", schema.get("security", []))
    return [key for requirement in requirements if isinstance(requirement, dict) for key in requirement]


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


def build_auth_provider(
    config: ApiKeyAuthConfig | HttpBasicAuthConfig | HttpBearerAuthConfig,
    scheme: Mapping[str, Any],
) -> AuthProvider:
    """Build an auth provider from config and OpenAPI scheme definition.

    This function is used by both v2 and v3 adapters as the logic is the same.

    Args:
        config: Auth configuration
        scheme: Security scheme definition

    Returns:
        AuthProvider instance for the given scheme

    """
    if isinstance(config, ApiKeyAuthConfig):
        return ApiKeyAuthProvider(value=config.api_key, name=scheme["name"], location=scheme["in"])

    elif isinstance(config, HttpBasicAuthConfig):
        return HttpBasicAuthProvider(username=config.username, password=config.password)

    elif isinstance(config, HttpBearerAuthConfig):
        return HttpBearerAuthProvider(bearer=config.bearer)

    # Should never reach here due to JSON Schema validation
    raise TypeError(f"Unknown auth config type: {type(config)}")  # pragma: no cover
