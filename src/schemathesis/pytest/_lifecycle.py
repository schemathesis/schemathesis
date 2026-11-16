"""Run lifecycle: `setup` and `teardown` hooks around the tests of a schema."""

from __future__ import annotations

from collections.abc import Callable, Generator, Hashable
from types import ModuleType
from typing import TYPE_CHECKING, Literal

import pytest

from schemathesis.config import ConfigError, SchemathesisConfig
from schemathesis.core.marks import Mark
from schemathesis.hooks import (
    EXTENSIONS,
    bind_extension,
    check_extension_tables,
    create_extension,
    dispatch_setup,
    dispatch_teardown,
    set_extension_listener,
    unbind_extensions,
)
from schemathesis.schemas import BaseSchema

if TYPE_CHECKING:
    from _pytest.fixtures import FixtureRequest

Scope = Literal["session", "package", "module", "class", "function"]
# Name of the fixture a lazily parametrized test takes its schema from.
SchemaFixtureMark = Mark[str](attr_name="schema_fixture")
_FIXTURES_KEY: pytest.StashKey[dict[Hashable, str]] = pytest.StashKey()
_EXTENSIONS_KEY: pytest.StashKey[_SessionExtensions] = pytest.StashKey()


class _SessionExtensions:
    """Extension instances of one pytest session, created as their classes register."""

    def __init__(self) -> None:
        try:
            self.settings = SchemathesisConfig.discover().extensions
        except ConfigError:
            # Reported when a schema loads
            self.settings = {}
        self.instances: dict[str, object] = {}
        for name, cls in EXTENSIONS.items():
            self.add(name, cls)
        self.previous_listener = set_extension_listener(self.add)

    def add(self, name: str, cls: type) -> None:
        self.instances[name] = instance = create_extension(name, cls, self.settings.get(name, {}))
        bind_extension(instance)

    def close(self) -> None:
        set_extension_listener(self.previous_listener)
        unbind_extensions(self.instances)


def start_extensions(config: pytest.Config) -> None:
    config.stash[_EXTENSIONS_KEY] = _SessionExtensions()


def stop_extensions(config: pytest.Config) -> None:
    # Another plugin's failing `pytest_configure` can end the session before extensions start
    extensions = config.stash.get(_EXTENSIONS_KEY, None)
    if extensions is not None:
        extensions.close()


def check_extension_settings(config: pytest.Config) -> None:
    try:
        check_extension_tables(config.stash[_EXTENSIONS_KEY].settings)
    except ConfigError as exc:
        raise pytest.UsageError(str(exc)) from None


def attach_schema(item: pytest.Item, schema: BaseSchema) -> None:
    """Run `setup` and `teardown` hooks of the schema around all its tests in the session."""
    # Every test function holds its own clone of the schema; clones share the hook dispatcher
    _attach(item, id(schema.hooks), "session", lambda request: schema)


def attach_schema_fixture(item: pytest.Function) -> None:
    """Run `setup` and `teardown` hooks for as long as the schema fixture of a lazily parametrized test lives."""
    name = SchemaFixtureMark.get(item.obj)
    if name is not None:
        definitions = item.session._fixturemanager.getfixturedefs(name, item)
        # An unknown fixture is reported by pytest when the test is set up
        if definitions:
            _attach(item, definitions[-1], definitions[-1].scope, lambda request: request.getfixturevalue(name))


def _attach(item: pytest.Item, key: Hashable, scope: Scope, resolve: Callable[[FixtureRequest], object]) -> None:
    names = item.config.stash.setdefault(_FIXTURES_KEY, {})
    name = names.get(key)
    if name is None:
        name = names[key] = f"_schemathesis_run_{len(names)}"
        item.config.pluginmanager.register(_run_fixture(name, scope, resolve), name)
    item.fixturenames.append(name)  # type: ignore[attr-defined]  # every test item pytest collects has it


def _run_fixture(name: str, scope: Scope, resolve: Callable[[FixtureRequest], object]) -> ModuleType:
    @pytest.fixture(scope=scope, name=name)
    def run(request: FixtureRequest) -> Generator[None, None, None]:
        schema = resolve(request)
        # Anything else is reported by the test itself
        if not isinstance(schema, BaseSchema):
            yield
            return
        try:
            dispatch_setup(schema)
        except BaseException:
            dispatch_teardown(schema)
            raise
        yield
        errors = dispatch_teardown(schema)
        for error in errors:
            # Shows where the hook failed and keeps errors of different hooks apart in the report
            error.__cause__ = error.__context__ = error.original_error
        if len(errors) == 1:
            raise errors[0]
        if errors:
            raise ExceptionGroup("`teardown` hooks failed", errors)

    # A module is the plugin shape pytest accepts fixtures of any scope from
    plugin = ModuleType(name)
    plugin.run = run  # type: ignore[attr-defined]  # dynamic module
    return plugin
