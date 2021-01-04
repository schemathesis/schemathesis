from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Set, Tuple, Union

from hypothesis.strategies import SearchStrategy

if TYPE_CHECKING:
    from .hooks import HookContext

PathLike = Union[Path, str]  # pragma: no mutate

Query = Dict[str, Any]  # pragma: no mutate
# Body can be of any Python type that corresponds to JSON Schema types + `bytes`
Body = Union[List, Dict[str, Any], str, int, float, bool, bytes]  # pragma: no mutate
PathParameters = Dict[str, Any]  # pragma: no mutate
Headers = Dict[str, Any]  # pragma: no mutate
Cookies = Dict[str, Any]  # pragma: no mutate
FormData = Dict[str, Any]  # pragma: no mutate


class NotSet:
    pass


# A filter for path / method
Filter = Union[str, List[str], Tuple[str], Set[str], NotSet]  # pragma: no mutate

Hook = Union[
    Callable[[SearchStrategy], SearchStrategy], Callable[[SearchStrategy, "HookContext"], SearchStrategy]
]  # pragma: no mutate

RawAuth = Tuple[str, str]  # pragma: no mutate
# Generic test with any arguments and no return
GenericTest = Callable[..., None]  # pragma: no mutate
