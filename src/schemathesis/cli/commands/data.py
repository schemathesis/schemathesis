from dataclasses import dataclass

from schemathesis.config import SchemathesisConfig


@dataclass(slots=True)
class Data:
    config: SchemathesisConfig
