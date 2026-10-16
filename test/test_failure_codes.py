import re

import pytest

from schemathesis.core.failures import Failure, ServerError
from schemathesis.failure_codes import get_failure_class, iter_failure_classes
from schemathesis.pytest.xdist import _rebuilt_failure_class

REGISTRY = [
    ("ST101", "server-error", "ServerError"),
    ("ST102", "server-error-content-type", "ContentTypeServerError"),
    ("ST103", "undocumented-status", "UndefinedStatusCode"),
    ("ST104", "missing-content-type", "MissingContentType"),
    ("ST105", "undocumented-content-type", "UndefinedContentType"),
    ("ST106", "malformed-media-type", "MalformedMediaType"),
    ("ST107", "missing-response-header", "MissingHeaders"),
    ("ST108", "response-schema", "JsonSchemaError"),
    ("ST109", "malformed-json", "MalformedJson"),
    ("ST110", "undeserializable-response", "ContentDeserializationError"),
    ("ST111", "unknown-discriminator", "DiscriminatorMismatch"),
    ("ST112", "slow-response", "ResponseTimeExceeded"),
    ("ST201", "accepted-invalid-request", "AcceptedNegativeData"),
    ("ST202", "rejected-valid-request", "RejectedPositiveData"),
    ("ST203", "missing-header-accepted", "MissingHeaderNotRejected"),
    ("ST204", "unsupported-method", "UnsupportedMethodResponse"),
    ("ST205", "invalid-allow-header", "AllowHeaderMismatch"),
    ("ST301", "use-after-free", "UseAfterFree"),
    ("ST302", "resource-unavailable", "EnsureResourceAvailability"),
    ("ST401", "ignored-auth", "IgnoredAuth"),
    ("ST402", "object-level-authorization", "ObjectLevelAuthorizationViolation"),
    ("ST501", "graphql-server-error", "GraphQLServerError"),
    ("ST502", "graphql-schema-violation", "GraphQLSchemaViolation"),
    ("ST503", "graphql-client-error", "GraphQLClientError"),
    ("ST504", "graphql-unexpected-response", "UnexpectedGraphQLResponse"),
    ("ST901", "custom-check", "CustomFailure"),
]


def test_registry_is_stable():
    assert [(cls.code, cls.slug, cls.__name__) for cls in iter_failure_classes()] == REGISTRY


@pytest.mark.parametrize("cls", iter_failure_classes(), ids=lambda cls: cls.__name__)
def test_failure_class_defines_its_own_code(cls):
    assert {"code", "slug", "kind_title", "wfc"} <= set(vars(cls))
    assert re.fullmatch(r"ST[1-59]\d\d", cls.code)


def test_codes_and_slugs_are_unique():
    classes = iter_failure_classes()
    assert len({cls.code for cls in classes}) == len({cls.slug for cls in classes}) == len(classes)


def test_lookup_by_code():
    assert (get_failure_class("ST101"), get_failure_class("ST999")) == (ServerError, None)


def test_user_subclass_inherits_code():
    class Direct(Failure):
        pass

    class Crash(ServerError):
        pass

    assert (Direct.code, Crash.code) == ("ST901", "ST101")


@pytest.mark.parametrize("cls", iter_failure_classes(), ids=lambda cls: cls.__name__)
def test_failure_class_defines_its_severity(cls):
    assert "severity_default" in vars(cls)


def test_failure_severity_defaults_to_class_severity():
    assert Failure(operation="GET /", title="Test", message="").severity == Failure.severity_default


def test_failures_rebuilt_from_xdist_workers_stay_out_of_the_registry():
    rebuilt = _rebuilt_failure_class("ST101")
    assert (rebuilt.__name__, rebuilt.code) == ("ServerError", "ST101")
    assert [cls.code for cls in iter_failure_classes()] == [code for code, _, _ in REGISTRY]
