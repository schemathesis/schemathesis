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
