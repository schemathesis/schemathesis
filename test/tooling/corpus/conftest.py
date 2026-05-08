from __future__ import annotations

import io
import tarfile
from pathlib import Path

import pytest


@pytest.fixture
def make_tarball():
    def _make(path: Path, entries: dict[str, bytes]) -> None:
        with tarfile.open(path, "w:gz") as archive:
            for name, payload in entries.items():
                info = tarfile.TarInfo(name)
                info.size = len(payload)
                archive.addfile(info, io.BytesIO(payload))

    return _make
