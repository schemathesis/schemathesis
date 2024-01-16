import pathlib
import re
from typing import Any


def require_relative_url(url: str) -> None:
    """Raise an error if the URL is not relative."""
    from yarl import URL

    if URL(url).is_absolute():
        raise ValueError("Schema path should be relative for WSGI/ASGI loaders")


def file_exists(path: str) -> bool:
    try:
        return pathlib.Path(path).is_file()
    except OSError:
        # For example, path could be too long
        return False


def is_filename(value: str) -> bool:
    """Detect if the input string is a filename by checking its extension."""
    return bool(pathlib.Path(value).suffix)


SURROGATE_PAIR_RE = re.compile(r"[\ud800-\udfff]")
has_surrogate_pair = SURROGATE_PAIR_RE.search


def is_illegal_surrogate(item: Any) -> bool:
    if isinstance(item, list):
        return any(isinstance(item_, str) and bool(has_surrogate_pair(item_)) for item_ in item)
    return isinstance(item, str) and bool(has_surrogate_pair(item))
