from __future__ import annotations

from schemathesis.core.failures import (
    AcceptedNegativeData,
    ContentTypeServerError,
    CustomFailure,
    Failure,
    MalformedJson,
    ResponseTimeExceeded,
    ServerError,
)
from schemathesis.graphql.checks import (
    GraphQLClientError,
    GraphQLDataMismatch,
    GraphQLSchemaViolation,
    GraphQLServerError,
    UnexpectedGraphQLResponse,
)
from schemathesis.grpc.checks import MalformedGrpcMessage
from schemathesis.openapi.checks import (
    AllowHeaderMismatch,
    ContentDeserializationError,
    DiscriminatorMismatch,
    EnsureResourceAvailability,
    IgnoredAuth,
    JsonSchemaError,
    MalformedMediaType,
    MissingContentType,
    MissingHeaderNotRejected,
    MissingHeaders,
    ObjectLevelAuthorizationViolation,
    RejectedPositiveData,
    UndefinedContentType,
    UndefinedStatusCode,
    UnsupportedMethodResponse,
    UseAfterFree,
)

# Built-in failure classes, ordered by code.
FAILURE_CLASSES: tuple[type[Failure], ...] = (
    ServerError,
    ContentTypeServerError,
    UndefinedStatusCode,
    MissingContentType,
    UndefinedContentType,
    MalformedMediaType,
    MissingHeaders,
    JsonSchemaError,
    MalformedJson,
    ContentDeserializationError,
    DiscriminatorMismatch,
    ResponseTimeExceeded,
    GraphQLServerError,
    GraphQLSchemaViolation,
    UnexpectedGraphQLResponse,
    GraphQLDataMismatch,
    MalformedGrpcMessage,
    AcceptedNegativeData,
    RejectedPositiveData,
    MissingHeaderNotRejected,
    UnsupportedMethodResponse,
    AllowHeaderMismatch,
    GraphQLClientError,
    UseAfterFree,
    EnsureResourceAvailability,
    IgnoredAuth,
    ObjectLevelAuthorizationViolation,
    CustomFailure,
)
_BY_CODE = {cls.code: cls for cls in FAILURE_CLASSES}


def get_failure_class(code: str) -> type[Failure] | None:
    return _BY_CODE.get(code)
