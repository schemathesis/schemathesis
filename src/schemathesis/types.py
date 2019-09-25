from pathlib import Path
from typing import Any, Dict, List, NewType, Set, Tuple, Union

Schema = NewType("Schema", Dict[str, Any])
PathLike = Union[Path, str]

Query = Dict[str, Any]
Body = Dict[str, Any]
PathParameters = Dict[str, Any]
Headers = Dict[str, Any]
Cookies = Dict[str, Any]

# A filter for endpoint / method
Filter = Union[str, List[str], Tuple[str], Set[str]]
