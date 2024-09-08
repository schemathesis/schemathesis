from inspect import iscoroutinefunction


def is_asgi_app(app: object) -> bool:
    return iscoroutinefunction(app) or (
        hasattr(app, "__call__") and iscoroutinefunction(app.__call__)  # noqa: B004
    )
