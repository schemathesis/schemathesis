from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from schemathesis.core.warnings import SchemathesisWarning

if TYPE_CHECKING:
    from schemathesis.python._constants.pool import ConstantsPool


@dataclass(slots=True)
class ConstantsExtractionWarning:
    """A registered `@schemathesis.python.constants` source that produced nothing usable."""

    source: str
    reason: str

    operation_label: str | None = None

    @property
    def kind(self) -> SchemathesisWarning:
        return SchemathesisWarning.CONSTANTS_EXTRACTION

    @property
    def message(self) -> str:
        return f"`{self.source}` {self.reason}"

    @property
    def group(self) -> str | None:
        return None


def iter_constants_warnings(pool: ConstantsPool) -> list[ConstantsExtractionWarning]:
    return [ConstantsExtractionWarning(source=failure.source, reason=failure.reason) for failure in pool.failures]
