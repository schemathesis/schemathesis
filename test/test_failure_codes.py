import re
import sys

import pytest

from schemathesis.core.failures import Failure, ServerError
from schemathesis.failure_codes import FAILURE_CLASSES, get_failure_class

REGISTRY = [
    ("ST101", "ServerError", ("not_a_server_error",)),
    ("ST102", "ContentTypeServerError", ("not_a_server_error",)),
    ("ST103", "UndefinedStatusCode", ("status_code_conformance",)),
    ("ST104", "MissingContentType", ("content_type_conformance", "response_schema_conformance")),
    ("ST105", "UndefinedContentType", ("content_type_conformance",)),
    ("ST106", "MalformedMediaType", ("content_type_conformance",)),
    ("ST107", "MissingHeaders", ("response_headers_conformance",)),
    ("ST108", "JsonSchemaError", ("response_schema_conformance", "response_headers_conformance")),
    ("ST109", "MalformedJson", ("response_schema_conformance",)),
    ("ST110", "ContentDeserializationError", ("response_schema_conformance",)),
    ("ST111", "DiscriminatorMismatch", ("response_schema_conformance",)),
    ("ST112", "ResponseTimeExceeded", ("max_response_time",)),
    ("ST113", "GraphQLServerError", ("not_a_server_error",)),
    ("ST114", "GraphQLSchemaViolation", ("response_schema_conformance",)),
    ("ST115", "UnexpectedGraphQLResponse", ("response_schema_conformance",)),
    ("ST201", "AcceptedNegativeData", ("negative_data_rejection",)),
    ("ST202", "RejectedPositiveData", ("positive_data_acceptance",)),
    ("ST203", "MissingHeaderNotRejected", ("missing_required_header",)),
    ("ST204", "UnsupportedMethodResponse", ("unsupported_method",)),
    ("ST205", "AllowHeaderMismatch", ("allow_header_conformance",)),
    ("ST206", "GraphQLClientError", ("positive_data_acceptance",)),
    ("ST301", "UseAfterFree", ("use_after_free",)),
    ("ST302", "EnsureResourceAvailability", ("ensure_resource_availability",)),
    ("ST401", "IgnoredAuth", ("ignored_auth",)),
    ("ST402", "ObjectLevelAuthorizationViolation", ("object_level_authorization",)),
    ("ST901", "CustomFailure", ()),
]


def test_registry_is_stable():
    assert [(cls.code, cls.__name__, cls.checks) for cls in FAILURE_CLASSES] == REGISTRY


@pytest.mark.parametrize("cls", FAILURE_CLASSES, ids=lambda cls: cls.__name__)
def test_failure_class_defines_its_own_code(cls):
    assert {"code", "checks"} <= set(vars(cls))
    assert re.fullmatch(r"ST[1-49]\d\d", cls.code)


def test_codes_are_unique():
    assert len({cls.code for cls in FAILURE_CLASSES}) == len(FAILURE_CLASSES)


def test_every_built_in_failure_class_is_listed():
    found, pending = set(), list(Failure.__subclasses__())
    while pending:
        cls = pending.pop()
        pending.extend(cls.__subclasses__())
        # Classes importable by name; skips ones defined in tests or rebuilt from xdist workers.
        if (
            cls.__module__.startswith("schemathesis.")
            and getattr(sys.modules[cls.__module__], cls.__qualname__, None) is cls
        ):
            found.add(cls)
    assert found == set(FAILURE_CLASSES)


def test_lookup_by_code():
    assert (get_failure_class("ST101"), get_failure_class("ST999")) == (ServerError, None)


def test_user_subclass_inherits_code():
    class Direct(Failure):
        pass

    class Crash(ServerError):
        pass

    assert (Direct.code, Crash.code) == ("ST901", "ST101")
