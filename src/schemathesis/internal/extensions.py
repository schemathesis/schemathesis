import os
from typing import Any, Callable


class ExtensionLoadingError(ImportError):
    """Raised when an extension cannot be loaded."""


def import_extension(path: str) -> Any:
    try:
        module, item = path.rsplit(".", 1)
        imported = __import__(module, fromlist=[item])
        return getattr(imported, item)
    except ValueError as exc:
        raise ExtensionLoadingError(f"Invalid path: {path}") from exc
    except (ImportError, AttributeError) as exc:
        raise ExtensionLoadingError(f"Could not import {path}") from exc


def extensible(env_var: str) -> Callable[[Any], Any]:
    def decorator(item: Any) -> Any:
        path = os.getenv(env_var)
        if path is not None:
            return import_extension(path)
        return item

    return decorator
