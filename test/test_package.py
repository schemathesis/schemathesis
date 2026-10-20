import os
import subprocess
import sys
from importlib import metadata
from pathlib import Path

SRC = str(Path(__file__).parent.parent / "src")


def test_dev_version(monkeypatch):
    # When Schemathesis is run in dev environment without installation
    monkeypatch.delitem(sys.modules, "schemathesis.core.version")

    def not_installed(name):
        raise metadata.PackageNotFoundError(name)

    monkeypatch.setattr(metadata, "version", not_installed)
    from schemathesis.core.version import SCHEMATHESIS_VERSION

    # Then it's version is "dev"
    assert SCHEMATHESIS_VERSION == "dev"


def test_builtin_check_importable_from_checks_module():
    # Built-in checks register lazily, so a fresh interpreter catches the import-order dependency
    code = "from schemathesis.checks import content_type_conformance"
    subprocess.run([sys.executable, "-c", code], check=True, env={**os.environ, "PYTHONPATH": SRC})


def test_openapi_checks_registered_without_cli():
    # Library users load schemas without importing the CLI or the pytest plugin
    code = (
        "import schemathesis\n"
        "from schemathesis.checks import CHECKS\n"
        "schemathesis.openapi.from_dict({'openapi': '3.0.2', 'info': {'title': 'T', 'version': '1'}, 'paths': {}})\n"
        "assert 'status_code_conformance' in {check.__name__ for check in CHECKS.get_all()}\n"
    )
    subprocess.run([sys.executable, "-c", code], check=True, env={**os.environ, "PYTHONPATH": SRC})
