import sys
from importlib import metadata


def test_dev_version(monkeypatch, mocker):
    # When Schemathesis is run in dev environment without installation
    monkeypatch.delitem(sys.modules, "schemathesis.constants")
    mocker.patch("importlib.metadata.version", side_effect=metadata.PackageNotFoundError)
    from schemathesis.constants import SCHEMATHESIS_VERSION

    # Then it's version is "dev"
    assert SCHEMATHESIS_VERSION == "dev"
