import sys

from importlib_metadata import PackageNotFoundError


def test_dev_version(monkeypatch, mocker):
    # When Schemathesis is run in dev environment without installation
    monkeypatch.delitem(sys.modules, "schemathesis")
    mocker.patch("importlib_metadata.version", side_effect=PackageNotFoundError)
    from schemathesis import __version__

    # Then it's version is "dev"
    assert __version__ == "dev"
