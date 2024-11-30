import os
import pathlib


def ensure_parent(path: os.PathLike, fail_silently: bool = True) -> None:
    # Try to create the parent dir
    try:
        pathlib.Path(path).parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    except OSError:
        if not fail_silently:
            raise


def file_exists(path: str) -> bool:
    try:
        return pathlib.Path(path).is_file()
    except OSError:
        # For example, path could be too long
        return False


def is_filename(value: str) -> bool:
    """Detect if the input string is a filename by checking its extension."""
    return bool(pathlib.Path(value).suffix)
