import warnings
from functools import wraps
from typing import Any, Callable, List, Mapping, Set, Tuple, Union

from .types import Filter

NOT_SET = object()


def deprecated(func: Callable, message: str) -> Callable:
    """Emit a warning if the given function is used."""

    @wraps(func)
    def inner(*args: Any, **kwargs: Any) -> Any:
        warnings.warn(message, DeprecationWarning)
        return func(*args, **kwargs)

    return inner


def is_schemathesis_test(func: Callable) -> bool:
    """Check whether test is parametrized with schemathesis."""
    try:
        return hasattr(func, "_schemathesis_test")
    except Exception:
        return False


def force_tuple(item: Filter) -> Union[List, Set, Tuple]:
    if not isinstance(item, (list, set, tuple)):
        return (item,)
    return item


def dict_true_values(**kwargs: Any) -> Mapping[str, Any]:
    """Create dict with given kwargs while skipping items where bool(value) evaluates to False."""
    return {key: value for key, value in kwargs.items() if bool(value)}
