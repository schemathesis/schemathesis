from typing import Any, Callable, Dict, TypeVar

from hypothesis import strategies as st

T = TypeVar("T")
Draw = Callable[[st.SearchStrategy[T]], T]
Schema = Dict[str, Any]
