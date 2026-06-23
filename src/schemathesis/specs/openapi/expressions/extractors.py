from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(slots=True)
class RegexExtractor:
    """Extract value via a regex."""

    value: re.Pattern

    def extract(self, value: str) -> str | None:
        match = self.value.search(value)
        if match is None:
            return None
        return match.group(1)
