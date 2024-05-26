from __future__ import annotations

from dataclasses import dataclass

from .cache import TransformCache
from .types import ObjectSchema


@dataclass
class TransformConfig:
    # The name of the keyword that represents nullable values
    # Usually `nullable` in Open API 3 and `x-nullable` in Open API 2
    nullable_key: str
    # Remove properties with the "writeOnly" flag set to `True`.
    # Write only properties are used in requests and should not be present in responses.
    remove_write_only: bool
    # Remove properties with the "readOnly" flag set to `True`.
    # Read only properties are used in responses and should not be present in requests.
    remove_read_only: bool
    # Components that could be potentially referenced by the schema
    components: dict[str, ObjectSchema]
    # Cache storing metadata about already transformed schemas
    cache: TransformCache
