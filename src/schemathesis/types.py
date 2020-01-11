from pathlib import Path
from typing import Any, Callable, Dict, List, NewType, Set, Tuple, Union

from hypothesis.strategies import SearchStrategy

Schema = NewType("Schema", Dict[str, Any])  # pragma: no mutate
PathLike = Union[Path, str]  # pragma: no mutate

Query = Dict[str, Any]  # pragma: no mutate
Body = Union[Dict[str, Any], bytes]  # pragma: no mutate
PathParameters = Dict[str, Any]  # pragma: no mutate
Headers = Dict[str, Any]  # pragma: no mutate
Cookies = Dict[str, Any]  # pragma: no mutate
FormData = Dict[str, Any]  # pragma: no mutate

# A filter for endpoint / method
Filter = Union[str, List[str], Tuple[str], Set[str], object]  # pragma: no mutate

Hook = Callable[[SearchStrategy], SearchStrategy]
