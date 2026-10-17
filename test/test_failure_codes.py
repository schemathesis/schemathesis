import re
import sys

import pytest

from schemathesis.core.failures import Failure, ServerError
from schemathesis.failure_codes import FAILURE_CLASSES, get_failure_class

REGISTRY = [
    ("ST101", "ServerError"),
    ("ST102", "ContentTypeServerError"),
    ("ST103", "UndefinedStatusCode"),
    ("ST104", "MissingContentType"),
    ("ST105", "UndefinedContentType"),
    ("ST106", "MalformedMediaType"),
    ("ST107", "MissingHeaders"),
    ("ST108", "JsonSchemaError"),
    ("ST109", "MalformedJson"),
    ("ST110", "ContentDeserializationError"),
    ("ST111", "DiscriminatorMismatch"),
    ("ST112", "ResponseTimeExceeded"),
    ("ST113", "GraphQLServerError"),
    ("ST114", "GraphQLSchemaViolation"),
    ("ST115", "UnexpectedGraphQLResponse"),
    ("ST201", "AcceptedNegativeData"),
    ("ST202", "RejectedPositiveData"),
    ("ST203", "MissingHeaderNotRejected"),
    ("ST204", "UnsupportedMethodResponse"),
    ("ST205", "AllowHeaderMismatch"),
    ("ST206", "GraphQLClientError"),
    ("ST301", "UseAfterFree"),
    ("ST302", "EnsureResourceAvailability"),
    ("ST401", "IgnoredAuth"),
    ("ST402", "ObjectLevelAuthorizationViolation"),
    ("ST901", "CustomFailure"),
]


def test_registry_is_stable():
    assert [(cls.code, cls.__name__) for cls in FAILURE_CLASSES] == REGISTRY


@pytest.mark.parametrize("cls", FAILURE_CLASSES, ids=lambda cls: cls.__name__)
def test_failure_class_defines_its_own_code(cls):
    assert "code" in vars(cls)
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
