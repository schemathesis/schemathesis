from __future__ import annotations

from typing import TYPE_CHECKING

from .formats import register_string_format as format
from .formats import unregister_string_format
from .media_types import register_media_type as media_type

if TYPE_CHECKING:
    from schemathesis.filters import HasAPIOperation, MatcherFunc

__all__ = [
    "format",
    "unregister_string_format",
    "media_type",
    "require_security_scheme",
]


def require_security_scheme(name: str) -> MatcherFunc:
    """Return a filter function matching operations that require the given security scheme.

    Checks operation-level ``security`` first, then falls back to schema-level ``security``.

    Args:
        name: Security scheme name as declared in ``securitySchemes`` / ``securityDefinitions``.

    Returns:
        A :data:`~schemathesis.filters.MatcherFunc` for use with ``.apply_to()`` / ``.skip_for()``.

    Example::

        schemathesis.auth.register()(SessionAuth).apply_to(
            schemathesis.openapi.require_security_scheme("session")
        )

    """

    def matcher(ctx: HasAPIOperation) -> bool:
        from schemathesis.specs.openapi.adapter.security import get_security_requirements

        schemes = get_security_requirements(ctx.operation.schema.raw_schema, ctx.operation.definition.raw)
        return name in schemes

    matcher.__name__ = matcher.__qualname__ = f"require_security_scheme({name!r})"
    return matcher
