from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from schemathesis.schemas import BaseSchema

# Keyed by `id(schema)` for O(1) dedup.
_PYTEST_SCHEMAS_KEY: pytest.StashKey[dict] = pytest.StashKey()


def track_schema(config: pytest.Config, schema: BaseSchema) -> None:
    tracked = config.stash.get(_PYTEST_SCHEMAS_KEY, None)
    if tracked is None:
        tracked = {}
        config.stash[_PYTEST_SCHEMAS_KEY] = tracked
    tracked.setdefault(id(schema), schema)
