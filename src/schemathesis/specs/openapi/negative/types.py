from collections.abc import Callable
from typing import Any, TypeVar

from hypothesis import strategies as st

T = TypeVar("T")
Draw = Callable[[st.SearchStrategy[T]], T]
Schema = dict[str, Any]
