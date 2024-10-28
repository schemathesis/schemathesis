from pathlib import Path

from ..types import PathLike


def ensure_parent(path: PathLike, fail_silently: bool = True) -> None:
    # Try to create the parent dir
    try:
        Path(path).parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    except OSError:
        if not fail_silently:
            raise
