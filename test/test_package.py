import os
import subprocess
import sys
from importlib import metadata
from pathlib import Path

SRC = str(Path(__file__).parent.parent / "src")


def test_dev_version(monkeypatch):
    monkeypatch.delitem(sys.modules, "schemathesis.core.version")

    def not_installed(name):
        raise metadata.PackageNotFoundError(name)

    monkeypatch.setattr(metadata, "version", not_installed)
    from schemathesis.core.version import SCHEMATHESIS_VERSION

    assert SCHEMATHESIS_VERSION == "dev"


def test_builtin_check_importable_from_checks_module():
    # Built-in checks register lazily, so a fresh interpreter catches the import-order dependency
    code = "from schemathesis.checks import content_type_conformance"
    subprocess.run([sys.executable, "-c", code], check=True, env={**os.environ, "PYTHONPATH": SRC})


def test_state_machine_importable_from_stateful_module():
    code = (
        "from schemathesis.stateful import APIStateMachine\n"
        "from schemathesis.generation.stateful import state_machine\n"
        "assert APIStateMachine is state_machine.APIStateMachine\n"
    )
    subprocess.run([sys.executable, "-c", code], check=True, env={**os.environ, "PYTHONPATH": SRC})


def test_builtin_checks_outlive_scoped_extensions():
    # The first schema load registers the built-in checks, and here it happens inside the scope
    code = (
        "import schemathesis\n"
        "from schemathesis.checks import CHECKS\n"
        "with schemathesis.scoped_extensions():\n"
        "    schemathesis.openapi.from_dict({'openapi': '3.0.2', 'info': {'title': 'T', 'version': '1'}, 'paths': {}})\n"
        "assert 'status_code_conformance' in {check.__name__ for check in CHECKS.get_all()}\n"
    )
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
