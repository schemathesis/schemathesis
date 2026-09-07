from __future__ import annotations

import os
import re
import threading
import time
from collections.abc import Callable
from functools import partial
from pathlib import Path

DEFAULT_ROOT = Path(".schemathesis")

# Windows refuses to replace or delete a file another run still holds open, so give that handle time to close.
LOCK_RETRY_TIMEOUT = 0.5
LOCK_RETRY_INTERVAL = 0.005

_SLUG_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def slug(name: str) -> str:
    return _SLUG_NON_ALNUM.sub("-", name.lower()).strip("-") or "default"


def project_directory(root: Path, project_title: str | None) -> Path:
    return root / slug(project_title or "default")


def retry_while_locked(operation: Callable[[], None]) -> None:
    # A refusal outliving the budget is not ours to hide - the caller decides whether losing the file is acceptable.
    deadline = time.monotonic() + LOCK_RETRY_TIMEOUT
    while True:
        try:
            operation()
            return
        except PermissionError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(LOCK_RETRY_INTERVAL)


def atomic_write_text(path: Path, data: str) -> None:
    # The temporary name is unique per writer so runs sharing a directory never consume each other's file.
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        tmp.write_text(data, encoding="utf-8")
        retry_while_locked(partial(os.replace, tmp, path))
    except OSError:
        tmp.unlink(missing_ok=True)
        raise
