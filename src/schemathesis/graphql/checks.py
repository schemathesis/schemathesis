from __future__ import annotations

from functools import cached_property
from typing import TYPE_CHECKING

from schemathesis.core.failures import Failure

if TYPE_CHECKING:
    from graphql.error import GraphQLFormattedError


class UnexpectedGraphQLResponse(Failure):
    """GraphQL response is not a JSON object."""

    def __init__(
        self,
        *,
        operation: str,
        type_name: str,
        title: str = "Unexpected GraphQL Response",
        message: str,
        code: str = "graphql_unexpected_response",
    ) -> None:
        self.operation = operation
        self.type_name = type_name
        self.title = title
        self.message = message
        self.code = code

    @property
    def _unique_key(self) -> str:
        return self.type_name


class GraphQLClientError(Failure):
    """GraphQL query has not been executed."""

    def __init__(
        self,
        *,
        operation: str,
        message: str,
        errors: list[GraphQLFormattedError],
        title: str = "GraphQL client error",
        code: str = "graphql_client_error",
    ) -> None:
        self.operation = operation
        self.errors = errors
        self.title = title
        self.message = message
        self.code = code

    @property
    def _unique_key(self) -> str:
        return self._cached_unique_key

    @cached_property
    def _cached_unique_key(self) -> str:
        return _group_graphql_errors(self.errors)


class GraphQLServerError(Failure):
    """GraphQL response indicates at least one server error."""

    def __init__(
        self,
        *,
        operation: str,
        message: str,
        errors: list[GraphQLFormattedError],
        title: str = "GraphQL server error",
        code: str = "graphql_server_error",
    ) -> None:
        self.operation = operation
        self.errors = errors
        self.title = title
        self.message = message
        self.code = code

    @property
    def _unique_key(self) -> str:
        return self._cached_unique_key

    @cached_property
    def _cached_unique_key(self) -> str:
        return _group_graphql_errors(self.errors)


def _group_graphql_errors(errors: list[GraphQLFormattedError]) -> str:
    entries = []
    for error in errors:
        message = error["message"]
        if "locations" in error:
            message += ";locations:"
            for location in sorted(error["locations"]):
                message += f"({location['line'], location['column']})"
        if "path" in error:
            message += ";path:"
            for chunk in error["path"]:
                message += str(chunk)
        entries.append(message)
    entries.sort()
    return "".join(entries)
