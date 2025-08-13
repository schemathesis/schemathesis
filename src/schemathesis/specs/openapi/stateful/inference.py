"""Inferencing connections between API operations."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Mapping, Union

from werkzeug.exceptions import MethodNotAllowed, NotFound
from werkzeug.routing import Map, MapAdapter, Rule

if TYPE_CHECKING:
    from schemathesis.specs.openapi.schemas import BaseOpenAPISchema


@dataclass(unsafe_hash=True)
class EndpointById:
    value: str
    path: str

    __slots__ = ("value", "path")


@dataclass(unsafe_hash=True)
class EndpointByRef:
    value: str
    path: str

    __slots__ = ("value", "path")


Endpoint = Union[EndpointById, EndpointByRef]


@dataclass
class Router:
    """Map URL paths to API Operation for OpenAPI link generation."""

    _adapter: MapAdapter

    __slots__ = ("_adapter",)

    @classmethod
    def from_schema(cls, schema: BaseOpenAPISchema) -> Router:
        # NOTE: Use `matchit` for routing in the future
        rules = []
        for method, path, definition in schema._operation_iter():
            if method != "get":
                continue
            operation_id = definition.get("operationId")
            endpoint: EndpointById | EndpointByRef
            if operation_id:
                endpoint = EndpointById(operation_id, path)
            else:
                encoded_path = path.replace("~", "~0").replace("/", "~1")
                endpoint = EndpointByRef(f"#/paths/{encoded_path}/{method}", path)

            # Replace `{parameter}` with `<parameter>` as angle brackets are used for parameters in werkzeug
            path = re.sub(r"\{([^}]+)\}", r"<\1>", path)
            rules.append(Rule(path, endpoint=endpoint))

        return cls(Map(rules).bind("", ""))

    def match(self, path: str) -> tuple[Endpoint, Mapping[str, str]] | None:
        """Match path to endpoint and extract path parameters."""
        try:
            return self._adapter.match(path)
        except (NotFound, MethodNotAllowed):
            return None

    def build_link(self, location: str) -> dict | None:
        """Build OpenAPI link definition from Location header."""
        match = self.match(location)
        if not match:
            return None

        endpoint, path_parameters = match

        link: dict[str, str | dict[str, Any]] = {}

        if isinstance(endpoint, EndpointById):
            link["operationId"] = endpoint.value
        else:
            link["operationRef"] = endpoint.value

        # If there are path parameters, build regex expressions to extract them
        if path_parameters:
            parameters = {}
            for name in path_parameters:
                # Replace the target parameter with capture group and others with non-slash matcher
                pattern = endpoint.path
                for candidate in path_parameters:
                    if candidate == name:
                        pattern = pattern.replace(f"{{{candidate}}}", "(.+)")
                    else:
                        pattern = pattern.replace(f"{{{candidate}}}", "[^/]+")

                parameters[name] = f"$response.header.Location#regex:{pattern}"

            link["parameters"] = parameters

        return link
