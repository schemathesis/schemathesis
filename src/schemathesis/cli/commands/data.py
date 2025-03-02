from dataclasses import dataclass

from schemathesis.config import SchemathesisConfig


@dataclass
class Data:
    config: SchemathesisConfig

    __slots__ = ("config",)
