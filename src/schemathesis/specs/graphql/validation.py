from __future__ import annotations

from typing import TYPE_CHECKING, Any, List, cast

from schemathesis.graphql.checks import GraphQLClientError, GraphQLServerError, UnexpectedGraphQLResponse

if TYPE_CHECKING:
    from schemathesis.models import Case


def validate_graphql_response(case: Case, payload: Any) -> None:
    """Validate GraphQL response.

    Semantically valid GraphQL responses are JSON objects and may contain `data` or `errors` keys.
    """
    from graphql.error import GraphQLFormattedError

    if not isinstance(payload, dict):
        raise UnexpectedGraphQLResponse(
            operation=case.operation.verbose_name,
            message="GraphQL response is not a JSON object",
            type_name=str(type(payload)),
        )

    errors = cast(List[GraphQLFormattedError], payload.get("errors"))
    if errors is not None and len(errors) > 0:
        data = payload.get("data")
        # There is no `path` pointing to some part of the input query, assuming client error
        if data is None and "path" not in errors[0]:
            raise GraphQLClientError(operation=case.operation.verbose_name, message=errors[0]["message"], errors=errors)
        if len(errors) > 1:
            message = "\n\n".join([f"{idx}. {error['message']}" for idx, error in enumerate(errors, 1)])
        else:
            message = errors[0]["message"]
        raise GraphQLServerError(operation=case.operation.verbose_name, message=message, errors=errors)
