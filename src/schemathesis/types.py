import enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Tuple, Union

PathLike = Union[Path, str]
Query = Dict[str, Any]
# Body can be of any Python type that corresponds to JSON Schema types + `bytes`
Body = Union[List, Dict[str, Any], str, int, float, bool, bytes]
PathParameters = Dict[str, Any]
Headers = Dict[str, Any]
Cookies = Dict[str, Any]
FormData = Dict[str, Any]
RequestCert = Union[str, Tuple[str, str]]
RawAuth = Tuple[str, str]
# Generic test with any arguments and no return
GenericTest = Callable[..., None]


class NotSet:
    pass


class Specification(str, enum.Enum):
    """Specification of the given schema."""

    OPENAPI = "openapi"
    GRAPHQL = "graphql"
