from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class Extractor:
    def extract(self, value: str) -> str | None:
        raise NotImplementedError


@dataclass
class RegexExtractor(Extractor):
    """Extract value via a regex."""

    value: re.Pattern

    def extract(self, value: str) -> str | None:
        match = self.value.search(value)
        if match is None:
            return None
        return match.group(1)
