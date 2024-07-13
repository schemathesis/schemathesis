import pytest

from schemathesis import internal, runner
from schemathesis.internal.extensions import ExtensionLoadingError, extensible
from schemathesis.specs.openapi._hypothesis import _PARAMETER_STRATEGIES_CACHE


@pytest.mark.operations("custom_format")
def test_clear_cache(openapi3_schema, simple_schema):
    list(runner.from_schema(openapi3_schema).execute())
    assert len(_PARAMETER_STRATEGIES_CACHE) >= 1
    internal.clear_cache()
    assert len(_PARAMETER_STRATEGIES_CACHE) == 0


def fast_func():
    return 43


ENV_VAR = "SCHEMATHESIS_EXTENSION_TEST_INTERNAL_FUNC"


def test_extensible_default():
    @extensible(ENV_VAR)
    def func():
        return 42

    assert func() == 42


def test_extensible_replace(monkeypatch):
    monkeypatch.setenv(ENV_VAR, "test.test_internal.fast_func")

    @extensible(ENV_VAR)
    def func():
        return 42

    assert func() == 43


@pytest.mark.parametrize("value", ("test.test_internal.wrong", "unknown.unknown", "unknown"))
def test_extensible_invalid(monkeypatch, value):
    monkeypatch.setenv(ENV_VAR, value)

    with pytest.raises(ExtensionLoadingError):

        @extensible(ENV_VAR)
        def func():
            return 42
