import os
import subprocess
import sys
from importlib import metadata
from pathlib import Path

SRC = str(Path(__file__).parent.parent / "src")


def test_dev_version(monkeypatch, mocker):
    # When Schemathesis is run in dev environment without installation
    monkeypatch.delitem(sys.modules, "schemathesis.core.version")
    mocker.patch("importlib.metadata.version", side_effect=metadata.PackageNotFoundError)
    from schemathesis.core.version import SCHEMATHESIS_VERSION

    # Then it's version is "dev"
    assert SCHEMATHESIS_VERSION == "dev"


def test_builtin_check_importable_from_checks_module():
    # Built-in checks register lazily, so a fresh interpreter catches the import-order dependency
    code = "from schemathesis.checks import content_type_conformance"
    subprocess.run([sys.executable, "-c", code], check=True, env={**os.environ, "PYTHONPATH": SRC})
