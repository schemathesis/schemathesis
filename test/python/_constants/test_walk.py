import importlib
import sys
from pathlib import Path

import pytest

from schemathesis.python._constants.walk import resolve_modules

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def _on_path():
    sys.path.insert(0, str(FIXTURES))
    yield
    sys.path.remove(str(FIXTURES))
    for name in list(sys.modules):
        if name == "sample_pkg" or name.startswith("sample_pkg."):
            sys.modules.pop(name)


def test_resolve_single_module_object():
    mod = importlib.import_module("sample_pkg.sub")
    assert resolve_modules([mod]) == {"sample_pkg.sub"}


def test_resolve_package_expands_to_submodules():
    pkg = importlib.import_module("sample_pkg")
    assert resolve_modules([pkg]) == {"sample_pkg", "sample_pkg.sub", "sample_pkg.values"}


def test_resolve_dotted_name_string():
    assert resolve_modules(["sample_pkg.sub"]) == {"sample_pkg.sub"}


def test_resolve_iterable_dedup():
    pkg = importlib.import_module("sample_pkg")
    sub = importlib.import_module("sample_pkg.sub")
    assert resolve_modules([pkg, sub, "sample_pkg.sub"]) == {"sample_pkg", "sample_pkg.sub", "sample_pkg.values"}


def test_unknown_dotted_name_silently_skipped():
    assert resolve_modules(["definitely_not_a_real_module_xyz"]) == set()


def test_non_module_non_string_input_ignored():
    # App objects are handled upstream by the orchestrator; the walker just ignores them.
    class App:
        pass

    assert resolve_modules([App()]) == set()


def test_import_failure_other_than_import_error_is_swallowed(tmp_path, monkeypatch):
    # A module whose top level raises (e.g. settings probe) must not crash the walker.
    bad_pkg = tmp_path / "raises_at_import"
    bad_pkg.mkdir()
    (bad_pkg / "__init__.py").write_text("raise RuntimeError('boom')\n")
    monkeypatch.syspath_prepend(str(tmp_path))

    assert resolve_modules(["raises_at_import"]) == set()


def test_generator_source_is_resolved_like_a_list():
    assert resolve_modules(name for name in ["sample_pkg.sub", "sample_pkg.values"]) == {
        "sample_pkg.sub",
        "sample_pkg.values",
    }
