from pathlib import Path
from typing import Any, Callable, Dict, List, NewType, Set, Tuple, Union

from hypothesis.strategies import SearchStrategy

Schema = NewType("Schema", Dict[str, Any])  # pragma: no mutate
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


# A filter for endpoint / method
Filter = Union[str, List[str], Tuple[str], Set[str], NotSet]  # pragma: no mutate

Hook = Callable[[SearchStrategy], SearchStrategy]  # pragma: no mutate

RawAuth = Tuple[str, str]  # pragma: no mutate
