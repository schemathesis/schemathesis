from typing import Any, List, cast

from ... import failures
from ...exceptions import get_grouped_graphql_error, get_unexpected_graphql_response_error


def validate_graphql_response(payload: Any) -> None:
    """Validate GraphQL response.

    Semantically valid GraphQL responses are JSON objects and may contain `data` or `errors` keys.
    """
    from graphql.error import GraphQLFormattedError

    if not isinstance(payload, dict):
        exc_class = get_unexpected_graphql_response_error(type(payload))
        raise exc_class(
            failures.UnexpectedGraphQLResponse.title,
            context=failures.UnexpectedGraphQLResponse(message="GraphQL response is not a JSON object"),
        )

    errors = cast(List[GraphQLFormattedError], payload.get("errors"))
    if errors is not None and len(errors) > 0:
        exc_class = get_grouped_graphql_error(errors)
        data = payload.get("data")
        # There is no `path` pointing to some part of the input query, assuming client error
        if data is None and "path" not in errors[0]:
            message = errors[0]["message"]
            raise exc_class(
                failures.GraphQLClientError.title,
                context=failures.GraphQLClientError(message=message, errors=errors),
            )
        if len(errors) > 1:
            message = "\n\n".join([f"{idx}. {error['message']}" for idx, error in enumerate(errors, 1)])
        else:
            message = errors[0]["message"]
        raise exc_class(
            failures.GraphQLServerError.title,
            context=failures.GraphQLServerError(message=message, errors=errors),
        )
