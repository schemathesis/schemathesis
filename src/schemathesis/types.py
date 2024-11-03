import enum
from pathlib import Path
from typing import Any, Dict, List, Tuple, Union

PathLike = Union[Path, str]
# Body can be of any Python type that corresponds to JSON Schema types + `bytes`
Body = Union[List, Dict[str, Any], str, int, float, bool, bytes]
RequestCert = Union[str, Tuple[str, str]]
RawAuth = Tuple[str, str]
# Generic test with any arguments and no return


class Specification(str, enum.Enum):
    """Specification of the given schema."""

    OPENAPI = "openapi"
    GRAPHQL = "graphql"
