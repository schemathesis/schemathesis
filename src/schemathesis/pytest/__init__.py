from __future__ import annotations

from collections.abc import Callable
from functools import wraps
from inspect import iscoroutinefunction
from typing import TYPE_CHECKING, Any

from schemathesis.pytest.loaders import from_fixture

if TYPE_CHECKING:
    from schemathesis.schemas import BaseSchema

__all__ = [
    "from_fixture",
    "parametrize",
]


def parametrize(**schemas: BaseSchema) -> Callable:
    """Return a decorator that parametrizes a test function over multiple named schemas.

    Example::

        @schemathesis.pytest.parametrize(
            users=schemathesis.openapi.from_wsgi("/openapi.json", users_app),
            orders=schemathesis.openapi.from_wsgi("/openapi.json", orders_app),
        )
        def test_api(case):
            case.call_and_validate()

    """

    def wrapper(func: Callable) -> Callable:
        from schemathesis.hooks import HookDispatcher
        from schemathesis.pytest.plugin import MultiSchemaHandleMark

        if iscoroutinefunction(func):

            @wraps(func)
            async def test_wrapper(*args: Any, **kwargs: Any) -> Any:
                return await func(*args, **kwargs)

        else:

            @wraps(func)
            def test_wrapper(*args: Any, **kwargs: Any) -> Any:
                return func(*args, **kwargs)

        HookDispatcher.add_dispatcher(test_wrapper)
        cloned = {name: schema.clone(test_function=test_wrapper) for name, schema in schemas.items()}
        MultiSchemaHandleMark.set(test_wrapper, cloned)
        return test_wrapper

    return wrapper
