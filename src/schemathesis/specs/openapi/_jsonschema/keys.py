from typing import cast

from .constants import MOVED_SCHEMAS_KEY_LENGTH, MOVED_SCHEMAS_PREFIX
from .types import SchemaKey


def _key_for_reference(reference: str, cutoff: int = MOVED_SCHEMAS_KEY_LENGTH) -> tuple[SchemaKey, bool]:
    """Extract the schema key from a reference."""
    if reference.startswith("file://"):
        reference = reference[7:]
    if reference.startswith(MOVED_SCHEMAS_PREFIX):
        is_moved = True
        key = reference[cutoff:]
    else:
        key = reference.replace("/", "-").replace("#", "")
        is_moved = False
    return cast(SchemaKey, key), is_moved


def _make_moved_reference(key: SchemaKey) -> str:
    return f"{MOVED_SCHEMAS_PREFIX}{key}"
