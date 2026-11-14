"""Infer links with regex extractors from `Location` headers and same-origin body URLs."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from werkzeug.exceptions import MethodNotAllowed, NotFound
from werkzeug.routing import Map, MapAdapter, RequestRedirect, Rule

from schemathesis.specs.openapi.operations import operation_reference
from schemathesis.specs.openapi.stateful.links import SCHEMATHESIS_LINK_EXTENSION

if TYPE_CHECKING:
    from schemathesis.engine.observations import BodyUrlEntry, LocationHeaderEntry
    from schemathesis.schemas import APIOperation
    from schemathesis.specs.openapi.schemas import OpenApiSchema


@dataclass(unsafe_hash=True, slots=True)
class OperationById:
    value: str
    method: str
    path: str

    def to_link_base(self) -> dict[str, Any]:
        return {"operationId": self.value, SCHEMATHESIS_LINK_EXTENSION: {"is_inferred": True}}


@dataclass(unsafe_hash=True, slots=True)
class OperationByRef:
    value: str
    method: str
    path: str

    def to_link_base(self) -> dict[str, Any]:
        return {"operationRef": self.value, SCHEMATHESIS_LINK_EXTENSION: {"is_inferred": True}}


OperationReference = OperationById | OperationByRef
# Method, path, response code, sorted path parameter names
SeenLinkKey = tuple[str, str, int, tuple[str, ...]]


@dataclass(slots=True)
class MatchList:
    """Operations matching a location path."""

    exact: OperationReference
    inexact: list[OperationReference]
    parameters: Mapping[str, Any]


@dataclass(slots=True)
class LinkInferencer:
    """Infers OpenAPI links from observed URLs."""

    _adapter: MapAdapter
    # Rule argument names to OpenAPI path parameter names, per operation
    _parameter_names: dict[OperationReference, dict[str, str]]
    # All API operations for prefix matching
    _operations: list[OperationReference]
    _base_url: str | None
    _base_path: str
    _links_keyword: str

    @classmethod
    def from_schema(cls, schema: OpenApiSchema) -> LinkInferencer:
        # Use `matchit` for routing in the future
        rules = []
        operations = []
        parameter_names: dict[OperationReference, dict[str, str]] = {}
        for method, path, definition in schema._operations.iter_operations():
            operation_id = definition.get("operationId")
            operation: OperationById | OperationByRef
            if operation_id:
                operation = OperationById(operation_id, method=method, path=path)
            else:
                operation = OperationByRef(operation_reference(path, method), method=method, path=path)

            operations.append(operation)

            # Werkzeug accepts only identifier-like argument names, while OpenAPI allows e.g. `item-id`
            parts = re.split(r"\{([^}]+)\}", path)
            parameter_names[operation] = {
                f"parameter_{index}": part for index, part in enumerate(parts) if index % 2 == 1
            }
            rule = "".join(f"<parameter_{index}>" if index % 2 == 1 else part for index, part in enumerate(parts))
            rules.append(Rule(rule, endpoint=operation, methods=[method.upper()]))

        return cls(
            _adapter=Map(rules).bind("", ""),
            _parameter_names=parameter_names,
            _operations=operations,
            _base_url=schema.config.base_url,
            _base_path=schema.base_path,
            _links_keyword=schema.adapter.links_keyword,
        )

    def match(self, path: str) -> tuple[OperationReference, Mapping[str, str]] | None:
        try:
            try:
                operation, arguments = self._adapter.match(path)
            except MethodNotAllowed as exc:
                # A path without GET matches through one of its own methods, picked in a stable order
                operation, arguments = self._adapter.match(path, method=min(exc.valid_methods or ()))
        except (NotFound, MethodNotAllowed, RequestRedirect):
            return None
        names = self._parameter_names[operation]
        return operation, {names[argument]: value for argument, value in arguments.items()}

    def _build_links_from_matches(self, matches: MatchList, expression: str) -> list[dict]:
        exact = self._build_link_from_match(matches.exact, matches.parameters, expression)
        parameters = exact["parameters"]
        links = [exact]
        for inexact in matches.inexact:
            link = inexact.to_link_base()
            # Prefix matches reuse the exact match's parameter extractors
            link["parameters"] = parameters
            links.append(link)
        return links

    def _find_matches_from_normalized_location(self, normalized_location: str) -> MatchList | None:
        match = self.match(normalized_location)
        if not match:
            # APIs should return Locations of existing operations; add partial matches if real APIs need them
            return None
        exact, parameters = match
        if not parameters:
            # A link without parameters carries no data
            return None
        matches = MatchList(exact=exact, inexact=[], parameters=parameters)

        # Location `/users/123` matches `/users/{user_id}` exactly and `/users/{user_id}/posts` by prefix
        for candidate in self._operations:
            if candidate == exact:
                continue
            if candidate.path.startswith(exact.path):
                matches.inexact.append(candidate)

        return matches

    def _build_link_from_match(
        self, operation: OperationById | OperationByRef, path_parameters: Mapping[str, Any], expression: str
    ) -> dict:
        link = operation.to_link_base()

        # The observed value may be an absolute URL and may or may not carry the base path
        prefix = "^(?:https?://[^/]+)?"
        base_path = self._base_path.rstrip("/")
        if base_path:
            prefix += f"(?:{re.escape(base_path)})?"
        # Literal path parts at even indices, parameter names at odd ones
        parts = re.split(r"\{([^}]+)\}", operation.path)
        parameters = {}
        for name in path_parameters:
            # Capture the target parameter up to the next segment, query or fragment
            pattern = "".join(
                re.escape(part) if index % 2 == 0 else "([^/?#]+)" if part == name else "[^/]+"
                for index, part in enumerate(parts)
            )
            parameters[name] = f"{expression}#regex:{prefix}{pattern}"

        link["parameters"] = parameters

        return link

    def _normalize_location(self, location: str) -> str | None:
        """Location as a path relative to the base path; None if empty or an absolute URL outside `base_url`."""
        location = location.strip()
        if not location:
            return None

        if location.startswith(("http://", "https://")):
            if not self._base_url:
                # Can't validate absolute URLs without base_url
                return None

            parsed = urlsplit(location)
            base_parsed = urlsplit(self._base_url)

            if parsed.scheme != base_parsed.scheme or parsed.netloc != base_parsed.netloc:
                return None

            return self._strip_base_path_from_location(parsed.path)

        stripped = self._strip_base_path_from_location(location)
        return stripped if stripped is not None else location

    def _strip_base_path_from_location(self, path: str) -> str | None:
        """None when `path` lies outside the base path."""
        base_path = self._base_path.rstrip("/")
        if path != base_path and not path.startswith(f"{base_path}/"):
            return None

        relative_path = path[len(base_path) :]
        return relative_path if relative_path.startswith("/") else "/" + relative_path

    def inject_links(self, operation: APIOperation, entries: list[LocationHeaderEntry] | list[BodyUrlEntry]) -> int:
        # Entries that would produce links already inferred in this call
        seen: set[SeenLinkKey] = set()
        injected = 0

        for entry in entries:
            location = self._normalize_location(entry.value)
            if location is None:
                continue

            matches = self._find_matches_from_normalized_location(location)
            if matches is None:
                continue

            key = (matches.exact.method, matches.exact.path, entry.status_code, tuple(sorted(matches.parameters)))
            if key in seen:
                continue
            seen.add(key)
            response = operation.responses.find_by_status_code(entry.status_code)
            links: dict[str, dict[str, dict]]
            if response is None:
                links = {}
                definition = {self._links_keyword: links}
                status_code = str(entry.status_code)
                operation.definition.raw.setdefault("responses", {})[status_code] = definition
                operation.responses.add(status_code, definition)
            else:
                links = response.source.setdefault(self._links_keyword, {})

            for link in self._build_links_from_matches(matches, entry.expression):
                # Inference runs again on every `--max-time` cycle with the same observations
                if link in links.values():
                    continue
                # Several observed URLs may land in the same response, so names must not collide
                links[f"X-Inferred-Link-{len(links)}"] = link
                injected += 1
        return injected
