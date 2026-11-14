from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(slots=True)
class RegexExtractor:
    """Extracts the first capture group of a regex match."""

    value: re.Pattern

    def extract(self, value: str) -> str | None:
        match = self.value.search(value)
        if match is None:
            return None
        return match.group(1)
