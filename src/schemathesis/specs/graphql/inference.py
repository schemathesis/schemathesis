"""Operation classification for GraphQL root fields."""

from __future__ import annotations

import enum
import re
from typing import Final

PRODUCER_PREFIXES: Final = frozenset(
    {"create", "add", "register", "new", "insert", "make", "clone", "duplicate", "import", "upload"}
)
CLEANUP_PREFIXES: Final = frozenset({"delete", "remove", "destroy", "purge", "clear", "revoke"})

# Camel-case tokenizers: extract the leading and trailing word tokens so the
# vocabularies can match verb-first (createBook) and verb-last (productCreate)
# naming. Each branch handles either an initial-cap-or-lowercase run
# (createBook -> create) or an all-caps run when no lowercase follows
# (CREATEbook -> CREATE, productCREATE -> CREATE).
_LEADING_TOKEN_RE: Final = re.compile(r"^(?:[A-Z]?[a-z]+|[A-Z]+)")
_TRAILING_TOKEN_RE: Final = re.compile(r"(?:[A-Z]?[a-z]+|[A-Z]+)$")


class RootType(enum.Enum):
    """GraphQL root operation type."""

    QUERY = enum.auto()
    MUTATION = enum.auto()


@enum.unique
class OperationRole(enum.IntEnum):
    """Role assigned to a GraphQL root field for layer ordering."""

    PRODUCER = 0
    READER = 1
    MUTATOR = 2
    CLEANUP = 3


def extract_entity(field_name: str, *, prefixes: frozenset[str]) -> str | None:
    """Strip a leading or trailing verb token and return the remaining name capitalized.

    `deleteBook` with `prefixes=CLEANUP_PREFIXES` -> `Book`; `bookDelete` -> `Book`.
    Returns `None` when no matching verb is found or only one token is present.
    """
    leading = _LEADING_TOKEN_RE.match(field_name)
    trailing = _TRAILING_TOKEN_RE.search(field_name)
    if leading is None or trailing is None or leading.group(0) == trailing.group(0):
        return None
    if leading.group(0).lower() in prefixes:
        token = trailing.group(0)
    elif trailing.group(0).lower() in prefixes:
        token = leading.group(0)
    else:
        return None
    return token[:1].upper() + token[1:]


def classify_operation(*, field_name: str, root_type: RootType) -> OperationRole:
    """Classify a GraphQL root field by its name and root type.

    Queries always classify as `READER`. Mutations classify by case-insensitive
    equality of either the leading or trailing camelCase token against the
    built-in vocabularies; trailing is consulted only when the leading token
    is distinct from it. Mutations that match neither fall through to
    `MUTATOR` (mid-pack).
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
