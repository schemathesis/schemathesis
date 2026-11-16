"""Process-wide registries that extension modules fill when imported."""

from __future__ import annotations

from collections.abc import Callable, Generator
from contextlib import contextmanager
from typing import TypeVar

from schemathesis.auths import GLOBAL_AUTH_STORAGE
from schemathesis.checks import CHECKS, load_all_checks
from schemathesis.core import deserialization, hooks
from schemathesis.core.media_types import MEDIA_TYPE_STRATEGIES
from schemathesis.hooks import EXTENSIONS, GLOBAL_HOOK_DISPATCHER
from schemathesis.python._constants.registry import default_registry
from schemathesis.specs.graphql.scalars import CUSTOM_SCALARS
from schemathesis.specs.openapi import formats
from schemathesis.transport import http_transports

Restore = Callable[[], None]
K = TypeVar("K")
V = TypeVar("V")


@contextmanager
def scoped_extensions() -> Generator[None, None, None]:
    """Remove global registrations made inside the `with` block when it exits, even on an exception.

    Covers hooks, auth providers, checks and the other global registries listed in the hooks reference.

    Example:
        ```python
        import schemathesis

        with schemathesis.scoped_extensions():
            import suite_hooks
            ...
        ```

        To open the scope in one call and close it in another, enter it on an `ExitStack`:

        ```python
        from contextlib import ExitStack

        extensions = ExitStack()
        extensions.enter_context(schemathesis.scoped_extensions())
        ...
        extensions.close()
        ```

    """
    restores = _save()
    try:
        yield
    finally:
        for restore in restores:
            restore()


def _save() -> list[Restore]:
    """Copy every registry; each returned function puts one copy back."""
    # Both register on first use, which may happen inside the scope, yet they belong to the whole process
    load_all_checks()
    hooks.load_from_env()
    return [
        _save_hooks(),
        _save_auth_providers(),
        _save_mapping(EXTENSIONS),
        _save_mapping(CHECKS._items),
        _save_mapping(deserialization._DESERIALIZERS),
        *(_save_mapping(transport._serializers) for transport in http_transports()),
        _save_formats(),
        _save_mapping(MEDIA_TYPE_STRATEGIES),
        _save_mapping(CUSTOM_SCALARS),
        _save_constant_sources(),
        _save_loaded_hook_files(),
    ]


def _save_mapping(mapping: dict[K, V]) -> Restore:
    saved = dict(mapping)

    def restore() -> None:
        mapping.clear()
        mapping.update(saved)

    return restore


def _save_hooks() -> Restore:
    # Unregistering edits these lists in place
    saved = {name: list(functions) for name, functions in GLOBAL_HOOK_DISPATCHER.get_all().items()}

    def restore() -> None:
        # Read again: removing all hooks replaces the mapping
        current = GLOBAL_HOOK_DISPATCHER.get_all()
        current.clear()
        current.update(saved)

    return restore


def _save_auth_providers() -> Restore:
    saved = list(GLOBAL_AUTH_STORAGE.providers)

    def restore() -> None:
        GLOBAL_AUTH_STORAGE.providers = saved

    return restore


def _save_formats() -> Restore:
    saved = dict(formats.STRING_FORMATS)

    def restore() -> None:
        if saved != formats.STRING_FORMATS:
            formats.STRING_FORMATS.clear()
            formats.STRING_FORMATS.update(saved)
            formats._invalidate_caches()

    return restore


def _save_constant_sources() -> Restore:
    registry = default_registry()
    saved = registry.get_all()

    def restore() -> None:
        # Re-registering bumps the registry version, which drops constants extracted from the removed sources
        if registry.get_all() != saved:
            registry.clear()
            for source in saved:
                registry.register(source)

    return restore


def _save_loaded_hook_files() -> Restore:
    # A hooks file loads once; if its registrations are removed, the next config has to load it again
    saved = set(hooks._LOADED_FILES)

    def restore() -> None:
        hooks._LOADED_FILES.intersection_update(saved)

    return restore
