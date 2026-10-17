import re
import sys

import pytest

from schemathesis.core.failures import Failure, ServerError
from schemathesis.failure_codes import FAILURE_CLASSES, get_failure_class


def test_codes_are_unique():
    assert len({cls.code for cls in FAILURE_CLASSES}) == len(FAILURE_CLASSES)


@pytest.mark.parametrize("cls", FAILURE_CLASSES, ids=lambda cls: cls.__name__)
def test_failure_class_defines_its_own_code(cls):
    assert {"code", "checks", "kind_title"} <= set(vars(cls))
    assert re.fullmatch(r"ST[1-49]\d\d", cls.code)


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
