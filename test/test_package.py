import sys

from schemathesis._compat import metadata


def test_dev_version(monkeypatch, mocker):
    # When Schemathesis is run in dev environment without installation
    monkeypatch.delitem(sys.modules, "schemathesis.constants")
    if sys.version_info < (3, 8):
        path = "importlib_metadata.version"
    else:
        path = "importlib.metadata.version"
    mocker.patch(path, side_effect=metadata.PackageNotFoundError)
    from schemathesis.constants import __version__

    # Then it's version is "dev"
    assert __version__ == "dev"
