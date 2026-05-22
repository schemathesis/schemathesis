import sys
from pathlib import Path

import pytest

from schemathesis.python._constants.extract import extract_from_module
from schemathesis.python._constants.pool import Origin

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def _on_path():
    sys.path.insert(0, str(FIXTURES))
    yield
    sys.path.remove(str(FIXTURES))
    for name in list(sys.modules):
        if name == "sample_pkg" or name.startswith("sample_pkg."):
            sys.modules.pop(name)


def test_extract_returns_typed_entries():
    o = Origin(source="src", module="sample_pkg.values", adapter=None)
    entries = list(extract_from_module("sample_pkg.values", origin=o))
    values = {(e.type, e.value) for e in entries}
    assert ("string", "active") in values
    assert ("string", "inactive") in values
    assert ("integer", 12345) in values
    assert ("float", 3.14159) in values
    assert ("bytes", b"tok_") in values


def test_extract_records_origin_on_each_entry():
    o = Origin(source="src", module="sample_pkg.values", adapter="flask")
    entries = list(extract_from_module("sample_pkg.values", origin=o))
    assert entries
    for entry in entries:
        assert entry.origins == (o,)


def test_extract_missing_module_returns_empty():
    o = Origin(source="src", module="no.such.mod", adapter=None)
    assert list(extract_from_module("no.such.mod", origin=o)) == []


def test_extract_skips_stdlib_modules():
    o = Origin(source="src", module="json", adapter=None)
    assert list(extract_from_module("json", origin=o)) == []


def test_extract_skips_builtin_modules_without_raising():
    o = Origin(source="src", module="math", adapter=None)
    assert list(extract_from_module("math", origin=o)) == []
