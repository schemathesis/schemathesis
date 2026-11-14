"""Operation classification for GraphQL root fields."""

from __future__ import annotations

import enum
import re
from typing import Final

PRODUCER_PREFIXES: Final = frozenset(
    {"create", "add", "register", "new", "insert", "make", "clone", "duplicate", "import", "upload"}
)
CLEANUP_PREFIXES: Final = frozenset({"delete", "remove", "destroy", "purge", "clear", "revoke"})
UPDATE_PREFIXES: Final = frozenset({"update", "edit", "modify"})

# Leading and trailing camelCase tokens, for verb-first (`createBook`) and verb-last (`productCreate`) names;
# an all-caps run counts as one token (`CREATEbook` -> `CREATE`).
_LEADING_TOKEN_RE: Final = re.compile(r"^(?:[A-Z]?[a-z]+|[A-Z]+)")
_TRAILING_TOKEN_RE: Final = re.compile(r"(?:[A-Z]?[a-z]+|[A-Z]+)$")


class RootType(enum.Enum):
    QUERY = enum.auto()
    MUTATION = enum.auto()


@enum.unique
class OperationRole(enum.IntEnum):
    """Role assigned to a GraphQL root field for layer ordering."""

    PRODUCER = 0
    READER = 1
    MUTATOR = 2
    CLEANUP = 3


def entity_candidates(field_name: str, *, prefixes: frozenset[str]) -> tuple[str, ...]:
    """Strip a leading or trailing verb token and name the entity the rest refers to, most specific first.

    `deleteBookCover` -> `("BookCover", "Cover")`; empty when no verb in `prefixes` matches or nothing remains.
    """
    leading = _LEADING_TOKEN_RE.match(field_name)
    trailing = _TRAILING_TOKEN_RE.search(field_name)
    if leading is None or trailing is None:
        return ()
    if leading.group(0).lower() in prefixes:
        rest = field_name[leading.end() :]
    elif trailing.group(0).lower() in prefixes:
        rest = field_name[: trailing.start()]
    else:
        return ()
    head = _TRAILING_TOKEN_RE.search(rest)
    if head is None:
        return ()
    entity = rest[:1].upper() + rest[1:]
    head_noun = head.group(0)[:1].upper() + head.group(0)[1:]
    return (entity,) if head_noun == entity else (entity, head_noun)


def classify_operation(*, field_name: str, root_type: RootType) -> OperationRole:
    """Classify a GraphQL root field by its name and root type.

    Queries are `READER`s. Mutations match their leading, then trailing, camelCase token case-insensitively
    against the vocabularies, else `MUTATOR`.
    """
    if root_type == RootType.QUERY:
        return OperationRole.READER

    leading = _LEADING_TOKEN_RE.match(field_name)
    if leading is not None:
        token = leading.group(0).lower()
        if token in PRODUCER_PREFIXES:
            return OperationRole.PRODUCER
        if token in CLEANUP_PREFIXES:
            return OperationRole.CLEANUP

    trailing = _TRAILING_TOKEN_RE.search(field_name)
    if trailing is not None and trailing.start() != 0:
        token = trailing.group(0).lower()
        if token in PRODUCER_PREFIXES:
            return OperationRole.PRODUCER
        if token in CLEANUP_PREFIXES:
            return OperationRole.CLEANUP

    return OperationRole.MUTATOR
