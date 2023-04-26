from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Dict, Iterable, List, Set, Tuple, Union

from hypothesis.strategies import SearchStrategy

if TYPE_CHECKING:
    from . import DataGenerationMethod
    from .hooks import HookContext

PathLike = Union[Path, str]

Query = Dict[str, Any]
# Body can be of any Python type that corresponds to JSON Schema types + `bytes`
Body = Union[List, Dict[str, Any], str, int, float, bool, bytes]
PathParameters = Dict[str, Any]
Headers = Dict[str, Any]
Cookies = Dict[str, Any]
FormData = Dict[str, Any]


class NotSet:
    pass


RequestCert = Union[str, Tuple[str, str]]


# A filter for path / method
Filter = Union[str, List[str], Tuple[str], Set[str], NotSet]

Hook = Union[Callable[[SearchStrategy], SearchStrategy], Callable[[SearchStrategy, "HookContext"], SearchStrategy]]

RawAuth = Tuple[str, str]
# Generic test with any arguments and no return
GenericTest = Callable[..., None]
DataGenerationMethodInput = Union["DataGenerationMethod", Iterable["DataGenerationMethod"]]
