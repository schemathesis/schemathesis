from __future__ import annotations

from typing import Any, cast

from schemathesis.generation.case import Case
from schemathesis.graphql.checks import GraphQLClientError, GraphQLServerError, UnexpectedGraphQLResponse


def is_client_error(payload: dict) -> bool:
    """Check if the GraphQL response indicates a client error (query validation/syntax error).

    Client errors occur when the query itself is invalid (missing required args, wrong types, etc.)
    and the GraphQL layer rejects it before any resolver executes.

    Server errors have a `path` field pointing to the resolver that failed, or return partial `data`.
    """
    errors = payload.get("errors")
    if not errors or len(errors) == 0:
        return False
    data = payload.get("data")
    # No `path` means the error occurred during query validation, not resolver execution
    return data is None and "path" not in errors[0]


def validate_graphql_response(case: Case, payload: Any) -> None:
    """Validate GraphQL response.

    Semantically valid GraphQL responses are JSON objects and may contain `data` or `errors` keys.
    """
    from graphql.error import GraphQLFormattedError

    if not isinstance(payload, dict):
        raise UnexpectedGraphQLResponse(
            operation=case.operation.label,
            message="GraphQL response is not a JSON object",
            type_name=str(type(payload)),
        )

    errors = cast(list[GraphQLFormattedError], payload.get("errors"))
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
