from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from schemathesis.core.transport import Response, load_json_lossy
from schemathesis.generation.case import Case
from schemathesis.graphql.checks import GraphQLClientError, GraphQLServerError, UnexpectedGraphQLResponse

if TYPE_CHECKING:
    from graphql.error import GraphQLFormattedError


def parse_payload(response: Response) -> Any:
    """Parse a GraphQL response body, re-parsing from raw bytes if the response lies about its charset."""
    try:
        return response.json()
    except (LookupError, ValueError):
        return load_json_lossy(response.content, response.encoding)


# Error markers meaning the caller sent something the server refused, as emitted by Apollo Server (`code`)
# and graphql-java based servers (`classification`). Authentication and authorization codes are deliberately
# absent: a request that never reached the resolvers says nothing about the data it carried.
CLIENT_ERROR_MARKERS = frozenset(
    {
        "BAD_USER_INPUT",
        "GRAPHQL_VALIDATION_FAILED",
        "GRAPHQL_PARSE_FAILED",
        "BAD_REQUEST",
        "PERSISTED_QUERY_NOT_FOUND",
        "PERSISTED_QUERY_NOT_SUPPORTED",
        "OPERATION_RESOLUTION_FAILURE",
        "ValidationError",
        "InvalidSyntax",
        "OperationNotSupported",
    }
)
# Error markers meaning the server itself failed while serving the query
SERVER_ERROR_MARKERS = frozenset(
    {
        "INTERNAL_SERVER_ERROR",
        "DataFetchingException",
        "NullValueInNonNullableField",
    }
)


def _error_markers(errors: list[GraphQLFormattedError]) -> list[str]:
    """Every classification the server attached to its errors, in the order they appear."""
    return [
        marker
        for error in errors
        if isinstance(error, dict) and isinstance(error.get("extensions"), dict)
        for marker in (error["extensions"].get("code"), error["extensions"].get("classification"))
        if isinstance(marker, str)
    ]


def is_client_error(payload: dict) -> bool:
    """Check if the GraphQL response indicates a client error (query validation/syntax error).

    Client errors occur when the query itself is invalid (missing required args, wrong types, etc.)
    and the server refuses to serve it.

    Servers that label their errors are trusted, and a label reporting a server-side failure outweighs any
    rejection reported beside it. Otherwise, server errors have a `path` field pointing to the resolver that
    failed, or return partial `data`.
    """
    errors = payload.get("errors")
    if not errors or len(errors) == 0:
        return False
    markers = _error_markers(errors)
    if any(marker in SERVER_ERROR_MARKERS for marker in markers):
        return False
    if any(marker in CLIENT_ERROR_MARKERS for marker in markers):
        return True
    data = payload.get("data")
    # No `path` means the error occurred during query validation, not resolver execution
    return data is None and "path" not in errors[0]


def validate_graphql_response(case: Case, payload: object) -> None:
    """Validate GraphQL response.

    Semantically valid GraphQL responses are JSON objects and may contain `data` or `errors` keys.
    """
    if not isinstance(payload, dict):
        raise UnexpectedGraphQLResponse(
            operation=case.operation.label,
            message="GraphQL response is not a JSON object",
            type_name=str(type(payload)),
        )

    errors = cast("list[GraphQLFormattedError]", payload.get("errors"))
    if errors is not None and len(errors) > 0:
        # Check if this is a client error (query validation failed)
        if is_client_error(payload):
            raise GraphQLClientError(operation=case.operation.label, message=errors[0]["message"], errors=errors)
        # Otherwise it's a server error (resolver execution failed)
        if len(errors) > 1:
            message = "\n\n".join([f"{idx}. {error['message']}" for idx, error in enumerate(errors, 1)])
        else:
            message = errors[0]["message"]
        raise GraphQLServerError(operation=case.operation.label, message=message, errors=errors)
