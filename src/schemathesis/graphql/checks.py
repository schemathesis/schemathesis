from __future__ import annotations

from typing import TYPE_CHECKING

from schemathesis.core.failures import Failure, Severity

if TYPE_CHECKING:
    from graphql.error import GraphQLFormattedError


class UnexpectedGraphQLResponse(Failure):
    """GraphQL response is not a JSON object."""

    code = "ST115"
    checks = ("response_schema_conformance",)
    kind_title = "Unexpected GraphQL response"

    __slots__ = ("type_name",)

    def __init__(
        self,
        *,
        operation: str,
        type_name: str,
        title: str | None = None,
        message: str,
        case_id: str | None = None,
    ) -> None:
        super().__init__(operation=operation, title=title, message=message, case_id=case_id, severity=Severity.MEDIUM)
        self.type_name = type_name

    @property
    def _unique_key(self) -> str:
        return self.type_name


class GraphQLClientError(Failure):
    """GraphQL query has not been executed."""

    code = "ST206"
    checks = ("positive_data_acceptance",)
    kind_title = "GraphQL client error"

    __slots__ = ("errors", "_unique_key_cache")

    def __init__(
        self,
        *,
        operation: str,
        message: str,
        errors: list[GraphQLFormattedError],
        title: str | None = None,
        case_id: str | None = None,
    ) -> None:
        super().__init__(operation=operation, title=title, message=message, case_id=case_id, severity=Severity.MEDIUM)
        self.errors = errors
        self._unique_key_cache: str | None = None

    @property
    def _unique_key(self) -> str:
        if self._unique_key_cache is None:
            self._unique_key_cache = _group_graphql_errors(self.errors)
        return self._unique_key_cache


class GraphQLServerError(Failure):
    """GraphQL response indicates at least one server error."""

    code = "ST113"
    checks = ("not_a_server_error",)
    kind_title = "GraphQL server error"

    __slots__ = ("errors", "_unique_key_cache")

    def __init__(
        self,
        *,
        operation: str,
        message: str,
        errors: list[GraphQLFormattedError],
        title: str | None = None,
        case_id: str | None = None,
    ) -> None:
        super().__init__(operation=operation, title=title, message=message, case_id=case_id, severity=Severity.CRITICAL)
        self.errors = errors
        self._unique_key_cache: str | None = None

    @property
    def _unique_key(self) -> str:
        if self._unique_key_cache is None:
            self._unique_key_cache = _group_graphql_errors(self.errors)
        return self._unique_key_cache


class GraphQLSchemaViolation(Failure):
    """GraphQL response contradicts the schema the server publishes."""

    code = "ST114"
    checks = ("response_schema_conformance",)
    kind_title = "Null in non-nullable GraphQL field"

    __slots__ = ("errors", "_unique_key_cache")

    def __init__(
        self,
        *,
        operation: str,
        message: str,
        errors: list[GraphQLFormattedError],
        title: str | None = None,
        case_id: str | None = None,
    ) -> None:
        super().__init__(operation=operation, title=title, message=message, case_id=case_id, severity=Severity.HIGH)
        self.errors = errors
        self._unique_key_cache: str | None = None

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
