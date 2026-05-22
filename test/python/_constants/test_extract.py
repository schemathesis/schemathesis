import pytest

from schemathesis.python._constants import extract
from schemathesis.python._constants.pool import Origin

DEPENDENT_PACKAGE = "test.python._constants.fixtures.dep_pkg"
ORIGIN = Origin(source="s", module="m", adapter=None)


def test_symlinked_third_party_module_is_not_local(tmp_path, monkeypatch):
    root = tmp_path / "site-packages"
    real_package = root / "thirdparty"
    real_package.mkdir(parents=True)
    (real_package / "__init__.py").touch()

    link_parent = tmp_path / "elsewhere"
    link_parent.mkdir()
    (link_parent / "thirdparty").symlink_to(real_package)
    symlinked_source = link_parent / "thirdparty" / "__init__.py"

    monkeypatch.setattr(extract, "_THIRD_PARTY_ROOTS", (str(root),))
    assert extract._is_local_module("thirdparty", str(symlinked_source)) is False


def test_real_local_module_stays_local(tmp_path, monkeypatch):
    root = tmp_path / "site-packages"
    root.mkdir()
    project_file = tmp_path / "myapp" / "views.py"
    project_file.parent.mkdir()
    project_file.touch()

    monkeypatch.setattr(extract, "_THIRD_PARTY_ROOTS", (str(root),))
    monkeypatch.setattr(extract, "_STDLIB_PATH", "")
    assert extract._is_local_module("myapp.views", str(project_file)) is True


def test_local_imports_of_returns_only_local_dependencies():
    # Resolves the relative `from .shared` import while dropping the third-party `flask` one.
    assert extract.local_imports_of(f"{DEPENDENT_PACKAGE}.flask_app") == {f"{DEPENDENT_PACKAGE}.shared"}


def test_local_imports_of_follows_conditional_imports():
    # `if TYPE_CHECKING` and `try`/`except` guard blocks still contribute their local imports.
    imports = extract.local_imports_of(f"{DEPENDENT_PACKAGE}.conditional")
    assert {f"{DEPENDENT_PACKAGE}.shared", f"{DEPENDENT_PACKAGE}.optional"} <= imports


@pytest.mark.parametrize("module_name", ["definitely_not_a_real_module_xyz", "sys"], ids=["unimportable", "no-source"])
def test_local_imports_of_returns_empty(module_name):
    # Unimportable modules and built-ins (no readable source) contribute nothing.
    assert extract.local_imports_of(module_name) == set()


@pytest.mark.parametrize("module_name", ["definitely_not_a_real_module_xyz", "sys"], ids=["unimportable", "stdlib"])
def test_extract_from_module_yields_nothing(module_name):
    # Unimportable and stdlib/built-in modules yield no constants.
    assert list(extract.extract_from_module(module_name, origin=ORIGIN)) == []
