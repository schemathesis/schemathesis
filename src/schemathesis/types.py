from pathlib import Path
from typing import Any, Dict, List, NewType, Set, Tuple, Union

Schema = NewType("Schema", Dict[str, Any])  # pragma: no mutate
PathLike = Union[Path, str]  # pragma: no mutate

Query = Dict[str, Any]  # pragma: no mutate
Body = Dict[str, Any]  # pragma: no mutate
PathParameters = Dict[str, Any]  # pragma: no mutate
Headers = Dict[str, Any]  # pragma: no mutate
Cookies = Dict[str, Any]  # pragma: no mutate
FormData = Dict[str, Any]  # pragma: no mutate

# A filter for endpoint / method
Filter = Union[str, List[str], Tuple[str], Set[str], object]  # pragma: no mutate
