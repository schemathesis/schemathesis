import sys
from importlib import metadata


def test_dev_version(monkeypatch, mocker):
    # When Schemathesis is run in dev environment without installation
    monkeypatch.delitem(sys.modules, "schemathesis.core.version")
    mocker.patch("importlib.metadata.version", side_effect=metadata.PackageNotFoundError)
    from schemathesis.core.version import SCHEMATHESIS_VERSION

    # Then it's version is "dev"
    assert SCHEMATHESIS_VERSION == "dev"
