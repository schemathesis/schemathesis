from __future__ import annotations

import re
import secrets
import string
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from schemathesis.config import ApiKeyAuthConfig, HttpBearerAuthConfig
from schemathesis.core.errors import InvalidSchema
from schemathesis.core.jsonschema import make_validator
from schemathesis.core.media_types import is_json
from schemathesis.core.parameters import ParameterLocation
from schemathesis.specs.openapi.auth_flow.detection import has_supplied_auth
from schemathesis.specs.openapi.auth_flow.models import AuthFlowSpec
from schemathesis.specs.openapi.auth_flow.vocabulary import is_email, is_secret, privileged_value

if TYPE_CHECKING:
    from schemathesis.core.jsonschema.types import JsonSchemaObject, JsonValue
    from schemathesis.schemas import APIOperation
    from schemathesis.specs.openapi.schemas import OpenApiSchema


@dataclass(slots=True)
class OpenApiAuthFlow:
    """Sign-up and login flow declared by an Open API schema."""

    schema: OpenApiSchema
    spec: AuthFlowSpec

    def is_supplied(self) -> bool:
        return has_supplied_auth(self.schema, self.spec.target_scheme)

    def config_lines(self) -> list[str]:
        spec = self.spec
        payload = ", ".join(
            f'{name} = "${{LOGIN_{re.sub("[^A-Za-z0-9]", "_", name).upper()}}}"' for name in spec.credentials
        )
        lines = [
            f"[auth.dynamic.openapi.{spec.target_scheme}]",
            f'path = "{spec.login_path}"',
            f"payload = {{ {payload} }}",
        ]
        if not is_json(spec.login_media_type):
            lines.append(f'payload_content_type = "{spec.login_media_type}"')
        lines.append(f'extract_selector = "{spec.token_pointer}"')
        return lines

    def sign_up_body(self, operation: APIOperation) -> tuple[dict[str, JsonValue], str] | None:
        # Servers reject blank profile fields, or issue unusable sessions without optional ones such as a role.
        from hypothesis.errors import InvalidArgument, Unsatisfiable

        from schemathesis.generation.hypothesis.examples import generate_one
        from schemathesis.specs.openapi._hypothesis import make_positive_strategy

        # Use the body the flow came from: the first one that declares properties.
        body = next(
            body for body in operation.body if isinstance(body.raw_schema, dict) and "properties" in body.raw_schema
        )
        schema = cast("JsonSchemaObject", body.optimized_schema)
        properties = {
            name: {**subschema, "minLength": 1}
            if isinstance(subschema, dict) and subschema.get("type") == "string" and "minLength" not in subschema
            else subschema
            for name, subschema in schema["properties"].items()
        }
        strategy = make_positive_strategy(
            {**schema, "type": "object", "required": list(properties), "properties": properties},
            operation.label,
            ParameterLocation.BODY,
            body.media_type,
            operation.schema.config.generation_for(operation=operation, phase="fuzzing"),
            operation.schema.adapter.jsonschema_validator_cls,
            name_to_uri=body.name_to_uri,
        )
        try:
            value = cast("dict[str, JsonValue]", generate_one(strategy))
        except (Unsatisfiable, InvalidArgument, InvalidSchema):
            return None
        # Accounts with an ordinary role get only part of the API, so take the most privileged one on offer.
        for name, subschema in schema["properties"].items():
            if isinstance(subschema, dict) and isinstance(subschema.get("enum"), list):
                privileged = privileged_value(subschema["enum"])
                if privileged is not None:
                    value[name] = cast("JsonValue", privileged)
        # Servers often enforce password and email rules the schema omits; realistic values pass them.
        minted = {**value, **{name: _mint(name) for name in self.spec.credentials}}
        validator = make_validator(schema, operation.schema.adapter.jsonschema_validator_cls)
        return (minted if validator.is_valid(minted) else value), body.media_type

    def authenticate(self, credentials: dict[str, JsonValue], token: str) -> None:
        spec = self.spec
        definition = self.schema.security.security_definitions[spec.target_scheme]
        self.schema.bootstrapped_credentials = {spec.login_operation: credentials}
        # Flows only target bearer and API key schemes.
        self.schema.bootstrapped_auth = {
            spec.target_scheme: HttpBearerAuthConfig(bearer=token)
            if definition.get("type") == "http"
            else ApiKeyAuthConfig(api_key=token)
        }


def _mint(name: str) -> str:
    alphabet = string.ascii_letters + string.digits
    if is_secret(name):
        return secrets.choice(string.ascii_uppercase) + "".join(secrets.choice(alphabet) for _ in range(14)) + "!1"
    if is_email(name):
        return f"{secrets.token_hex(6)}@{secrets.token_hex(4)}.test"
    return "".join(secrets.choice(alphabet) for _ in range(12))
