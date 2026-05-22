from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from . import shared

try:
    from . import optional
except ImportError:
    optional = None

__all__ = ["optional", "shared"]
