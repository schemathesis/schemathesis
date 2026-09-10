from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Any

from schemathesis.core.version import SCHEMATHESIS_VERSION

if TYPE_CHECKING:
    from schemathesis.core.failures import Failure

FORMAT_VERSION = 1

Identity = tuple[str, str, str, str]


def failure_identity(failure: Failure, check: str) -> Identity:
    """What makes two failures the same: the operation, the check, the failure class, and its signature."""
    return (failure.operation or "", check, type(failure).__name__, failure._unique_key)


@dataclass(slots=True)
class BaselineEntry:
    operation: str
    check: str
    failure: str
    signature: str
    first_seen: str | None = None
    last_seen: str | None = None
    expires: str | None = None
    # Anything else the file carries. Only `expires` means anything to Schemathesis; the rest is yours.
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def id(self) -> str:
        return hashlib.sha256("\0".join(self.identity).encode()).hexdigest()[:6]

    @property
    def identity(self) -> Identity:
        return (self.operation, self.check, self.failure, self.signature)

    @classmethod
    def from_failure(cls, failure: Failure, *, check: str, today: date | None = None) -> BaselineEntry:
        stamp = (today or date.today()).isoformat()
        return cls(
            operation=failure.operation or "",
            check=check,
            failure=type(failure).__name__,
            signature=failure._unique_key,
            first_seen=stamp,
            last_seen=stamp,
        )

    def is_expired(self, today: date) -> bool:
        return self.expires is not None and date.fromisoformat(self.expires) < today

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BaselineEntry:
        known = {name: data[name] for name in cls.__slots__ if name in data and name != "extra"}
        extra = {key: value for key, value in data.items() if key not in cls.__slots__ and key != "id"}
        return cls(**known, extra=extra)

    def to_dict(self) -> dict[str, Any]:
        stored: dict[str, Any] = {"id": self.id}
        for name in self.__slots__:
            value = getattr(self, name)
            if name != "extra" and value is not None:
                stored[name] = value
        stored.update(self.extra)
        return stored


@dataclass(slots=True)
class Baseline:
    entries: list[BaselineEntry]
    # Entries grouped by identity, built on first lookup. Recording and pruning happen after the
    # run, so nothing invalidates it mid-match.
    _index: dict[Identity, BaselineEntry] | None = None

    @classmethod
    def load(cls, path: Path) -> Baseline:
        if not path.exists():
            return cls(entries=[])
        raw = json.loads(path.read_text(encoding="utf-8"))
        version = raw.get("format_version")
        if version != FORMAT_VERSION:
            raise ValueError(
                f"Unsupported baseline format version {version}; this Schemathesis writes version {FORMAT_VERSION}"
            )
        return cls(entries=[BaselineEntry.from_dict(entry) for entry in raw["entries"]])

    def match(self, failure: Failure, check: str, *, today: date | None = None) -> BaselineEntry | None:
        """The entry that covers this failure, or `None` when it is new."""
        if self._index is None:
            self._index = {entry.identity: entry for entry in reversed(self.entries)}
        entry = self._index.get(failure_identity(failure, check))
        if entry is None or entry.is_expired(today or date.today()):
            return None
        return entry

    def save(self, path: Path) -> None:
        document = {
            "format_version": FORMAT_VERSION,
            "schemathesis_version": SCHEMATHESIS_VERSION,
            "entries": [entry.to_dict() for entry in sorted(self.entries, key=lambda entry: entry.identity)],
        }
        path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
