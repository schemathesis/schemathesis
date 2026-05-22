import pytest

from schemathesis.python._constants.registry import (
    SourceRegistration,
    SourceRegistry,
)


def test_decorator_registers_and_returns_function():
    registry = SourceRegistry()
    decorator = registry.decorator

    @decorator
    def my_source():
        return []

    assert callable(my_source)
    assert my_source() == []
    entries = registry.entries()
    assert len(entries) == 1
    entry = entries[0]
    assert isinstance(entry, SourceRegistration)
    assert entry.name == "my_source"
    assert entry.callable is my_source


def test_multiple_registrations_preserved_in_order():
    registry = SourceRegistry()
    decorator = registry.decorator

    @decorator
    def first():
        return []

    @decorator
    def second():
        return []

    assert [e.name for e in registry.entries()] == ["first", "second"]


def test_re_registration_of_same_callable_is_noop():
    registry = SourceRegistry()
    decorator = registry.decorator

    @decorator
    def only():
        return []

    decorator(only)
    assert len(registry.entries()) == 1


def test_decorator_rejects_non_callable():
    registry = SourceRegistry()
    with pytest.raises(TypeError):
        registry.decorator(42)  # type: ignore[arg-type]


def test_clear_removes_all_entries():
    registry = SourceRegistry()
    decorator = registry.decorator

    @decorator
    def a():
        return []

    registry.clear()
    assert registry.entries() == ()
