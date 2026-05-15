from __future__ import annotations

import os
import re
from pathlib import Path

DEFAULT_ROOT = Path(".schemathesis")

_SLUG_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def slug(name: str) -> str:
    return _SLUG_NON_ALNUM.sub("-", name.lower()).strip("-") or "default"


def project_directory(root: Path, project_title: str | None) -> Path:
    return root / slug(project_title or "default")


def atomic_write_text(path: Path, data: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(data, encoding="utf-8")
    os.replace(tmp, path)
