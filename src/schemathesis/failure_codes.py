from __future__ import annotations

import sys

# Importing these modules defines the OpenAPI and GraphQL failure classes.
import schemathesis.graphql.checks  # noqa: F401
import schemathesis.openapi.checks  # noqa: F401
from schemathesis.core.failures import Failure


def iter_failure_classes() -> list[type[Failure]]:
    """Built-in failure classes, ordered by code."""
    found: list[type[Failure]] = []
    pending = list(Failure.__subclasses__())
    while pending:
        cls = pending.pop()
        pending.extend(cls.__subclasses__())
        # Only classes importable by name; this skips classes rebuilt from pytest-xdist worker data.
        module = sys.modules.get(cls.__module__)
        if cls.__module__.startswith("schemathesis.") and getattr(module, cls.__qualname__, None) is cls:
            found.append(cls)
    return sorted(found, key=lambda cls: cls.code)


def get_failure_class(code: str) -> type[Failure] | None:
    return next((cls for cls in iter_failure_classes() if cls.code == code), None)
