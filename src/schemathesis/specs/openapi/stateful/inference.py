"""Inferencing connections between API operations.

The current implementation extracts information from the `Location` header and
generates OpenAPI links for exact and prefix matches.

When a `Location` header points to `/users/123`, the inference:

    1. Finds the exact match: `GET /users/{userId}`
    2. Finds prefix matches: `GET /users/{userId}/posts`, `GET /users/{userId}/posts/{postId}`
    3. Generates OpenAPI links with regex parameter extractors
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Mapping, Union
from urllib.parse import urlsplit

from werkzeug.exceptions import MethodNotAllowed, NotFound
from werkzeug.routing import Map, MapAdapter, Rule

if TYPE_CHECKING:
    from schemathesis.specs.openapi.schemas import BaseOpenAPISchema


@dataclass(unsafe_hash=True)
class EndpointById:
    value: str
    method: str
    path: str

    __slots__ = ("value", "method", "path")

    def to_link_base(self) -> dict[str, Any]:
        return {"operationId": self.value}


@dataclass(unsafe_hash=True)
class EndpointByRef:
    value: str
    method: str
    path: str

    __slots__ = ("value", "method", "path")

    def to_link_base(self) -> dict[str, Any]:
        return {"operationRef": self.value}


Endpoint = Union[EndpointById, EndpointByRef]


@dataclass
class MatchList:
    exact: Endpoint
    inexact: list[Endpoint]
    parameters: Mapping[str, Any]

    __slots__ = ("exact", "inexact", "parameters")


@dataclass
class Router:
    """Map URL paths to API Operation for OpenAPI link generation."""

    _adapter: MapAdapter
    # All endpoints for prefix matching
    _endpoints: list[Endpoint]
    _base_url: str | None
    _base_path: str

    __slots__ = ("_adapter", "_endpoints", "_base_url", "_base_path")

    @classmethod
    def from_schema(cls, schema: BaseOpenAPISchema) -> Router:
        # NOTE: Use `matchit` for routing in the future
        # TODO: Ensure parameter-less endpoints won't match just everything
        rules = []
        endpoints = []
        for method, path, definition in schema._operation_iter():
            operation_id = definition.get("operationId")
            endpoint: EndpointById | EndpointByRef
            if operation_id:
                endpoint = EndpointById(operation_id, method=method, path=path)
            else:
                encoded_path = path.replace("~", "~0").replace("/", "~1")
                endpoint = EndpointByRef(f"#/paths/{encoded_path}/{method}", method=method, path=path)

            endpoints.append(endpoint)

            # Replace `{parameter}` with `<parameter>` as angle brackets are used for parameters in werkzeug
            path = re.sub(r"\{([^}]+)\}", r"<\1>", path)
            rules.append(Rule(path, endpoint=endpoint, methods=[method.upper()]))

        return cls(
            Map(rules).bind("", ""),
            endpoints,
            schema.config.base_url,
            schema.base_path,
        )

    def match(self, path: str) -> tuple[Endpoint, Mapping[str, str]] | None:
        """Match path to endpoint and extract path parameters."""
        try:
            return self._adapter.match(path)
        except (NotFound, MethodNotAllowed):
            return None

    def find_all_matches(self, location: str) -> MatchList | None:
        """Find all possible matches (both exact and partial) for the location."""
        # Exact match first
        match = self.match(location)
        if not match:
            # It may happen that there is no match, but it is unlikely as the API assumed to return a valid Location
            # that points to existing endpoint. In such cases, if they appear in practice the logic here could be extended
            # to support partial matches
            return None
        exact, parameters = match
        matches = MatchList(exact=exact, inexact=[], parameters=parameters)

        # Find prefix matches, excluding the exact match
        # For example:
        #
        #  Location: /users/123 -> /users/{user_id} (exact match)
        #  /users/{user_id}/posts , /users/{user_id}/posts/{post_id} (partial matches)
        #
        for candidate in self._endpoints:
            if candidate.method != "get" or (
                candidate.path.startswith(exact.path) and len(candidate.path) != len(exact.path)
            ):
                matches.inexact.append(candidate)

        return matches

    def build_links(self, location: str) -> list[dict]:
        """Build all possible OpenAPI link definitions from Location header."""
        normalized_location = self._normalize_location(location)
        if normalized_location is None:
            return []
        matches = self.find_all_matches(normalized_location)
        if matches is None:
            return []
        exact = self._build_link_from_match(matches.exact, matches.parameters)
        parameters = exact.get("parameters")
        links = [exact]
        for inexact in matches.inexact:
            link = inexact.to_link_base()
            # Parameter extraction is the same, only operations are different
            if parameters is not None:
                link["parameters"] = parameters
            links.append(link)
        return links

    def _build_link_from_match(
        self, endpoint: EndpointById | EndpointByRef, path_parameters: Mapping[str, Any]
    ) -> dict:
        link = endpoint.to_link_base()

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

    def _normalize_location(self, location: str) -> str | None:
        """Normalize location header, handling both relative and absolute URLs."""
        location = location.strip()
        if not location:
            return None

        # Check if it's an absolute URL
        if location.startswith(("http://", "https://")):
            if not self._base_url:
                # Can't validate absolute URLs without base_url
                return None

            parsed = urlsplit(location)
            base_parsed = urlsplit(self._base_url)

            # Must match scheme, netloc, and start with the base path
            if parsed.scheme != base_parsed.scheme or parsed.netloc != base_parsed.netloc:
                return None

            base_path = self._base_path.rstrip("/")
            if not parsed.path.startswith(base_path):
                return None

            # Strip the base path to get relative path
            relative_path = parsed.path[len(base_path) :]
            return relative_path if relative_path.startswith("/") else "/" + relative_path
        # Relative URL - use as is
        return location
