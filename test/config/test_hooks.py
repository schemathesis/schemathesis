import sys

import pytest

from schemathesis.config import SchemathesisConfig
from schemathesis.core.errors import HookError


def test_error():
    with pytest.raises(HookError):
        SchemathesisConfig.from_str("hooks = 'test.config.hooks.error'")


def test_empty(capsys):
    SchemathesisConfig.from_str("hooks = 'test.config.hooks.hello'")
    captured = capsys.readouterr()
    assert "HELLO" in captured.out


def test_module_hooks_add_working_directory_to_import_path_once(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "path", [entry for entry in sys.path if entry != str(tmp_path)])
    SchemathesisConfig.from_str("hooks = 'test.config.hooks.example'")
    SchemathesisConfig.from_str("hooks = 'test.config.hooks.example'")
    assert sys.path.count(str(tmp_path)) == 1
