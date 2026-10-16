from __future__ import annotations

from typing import TYPE_CHECKING

from schemathesis.core.failures import Failure, Severity

if TYPE_CHECKING:
    from graphql.error import GraphQLFormattedError


class UnexpectedGraphQLResponse(Failure):
    """GraphQL response is not a JSON object."""

    code = "ST504"
    slug = "graphql-unexpected-response"
    kind_title = "Unexpected GraphQL Response"
    wfc = ()
    severity_default = Severity.MEDIUM

    __slots__ = ("operation", "type_name", "title", "message", "case_id", "severity")

    def __init__(
        self,
        *,
        operation: str,
        type_name: str,
        title: str = "Unexpected GraphQL Response",
        message: str,
        case_id: str | None = None,
    ) -> None:
        self.operation = operation
        self.type_name = type_name
        self.title = title
        self.message = message
        self.case_id = case_id
        self.severity = self.severity_default

    @property
    def _unique_key(self) -> str:
        return self.type_name


class GraphQLClientError(Failure):
    """GraphQL query has not been executed."""

    code = "ST503"
    slug = "graphql-client-error"
    kind_title = "GraphQL client error"
    wfc = ()
    severity_default = Severity.MEDIUM

    __slots__ = ("operation", "errors", "title", "message", "case_id", "_unique_key_cache", "severity")

    def __init__(
        self,
        *,
        operation: str,
        message: str,
        errors: list[GraphQLFormattedError],
        title: str = "GraphQL client error",
        case_id: str | None = None,
    ) -> None:
        self.operation = operation
        self.errors = errors
        self.title = title
        self.message = message
        self.case_id = case_id
        self._unique_key_cache: str | None = None
        self.severity = self.severity_default

    @property
    def _unique_key(self) -> str:
        if self._unique_key_cache is None:
            self._unique_key_cache = _group_graphql_errors(self.errors)
        return self._unique_key_cache


class GraphQLServerError(Failure):
    """GraphQL response indicates at least one server error."""

    code = "ST501"
    slug = "graphql-server-error"
    kind_title = "GraphQL server error"
    wfc = ()
    severity_default = Severity.CRITICAL

    __slots__ = ("operation", "errors", "title", "message", "case_id", "_unique_key_cache", "severity")

    def __init__(
        self,
        *,
        operation: str,
        message: str,
        errors: list[GraphQLFormattedError],
        title: str = "GraphQL server error",
        case_id: str | None = None,
    ) -> None:
        self.operation = operation
        self.errors = errors
        self.title = title
        self.message = message
        self.case_id = case_id
        self._unique_key_cache: str | None = None
        self.severity = self.severity_default

    @property
    def _unique_key(self) -> str:
        if self._unique_key_cache is None:
            self._unique_key_cache = _group_graphql_errors(self.errors)
        return self._unique_key_cache


class GraphQLSchemaViolation(Failure):
    """GraphQL response contradicts the schema the server publishes."""

    code = "ST502"
    slug = "graphql-schema-violation"
    kind_title = "GraphQL schema violation"
    wfc = ()
    severity_default = Severity.HIGH

    __slots__ = ("operation", "errors", "title", "message", "case_id", "_unique_key_cache", "severity")

    def __init__(
        self,
        *,
        operation: str,
        message: str,
        errors: list[GraphQLFormattedError],
        title: str = "GraphQL schema violation",
        case_id: str | None = None,
    ) -> None:
        self.operation = operation
        self.errors = errors
        self.title = title
        self.message = message
        self.case_id = case_id
        self._unique_key_cache: str | None = None
        self.severity = self.severity_default

    @property
    def _unique_key(self) -> str:
        if self._unique_key_cache is None:
            self._unique_key_cache = _group_graphql_errors(self.errors)
        return self._unique_key_cache


def _group_graphql_errors(errors: list[GraphQLFormattedError]) -> str:
    entries = []
    for error in errors:
        message = error["message"]
        if "locations" in error:
            message += ";locations:"
            for location in sorted(error["locations"], key=lambda location: (location["line"], location["column"])):
                message += f"({location['line'], location['column']})"
        if "path" in error:
            message += ";path:"
            for chunk in error["path"]:
                message += str(chunk)
        entries.append(message)
    entries.sort()
    return "".join(entries)
