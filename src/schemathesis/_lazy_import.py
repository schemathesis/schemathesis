from typing import Dict, Any, Callable


def lazy_import(module: str, name: str, imports: Dict[str, Callable[[], Any]], _globals: Dict[str, Any]) -> Any:
    value = _globals.get(name)
    if value is not None:
        return value
    loader = imports.get(name)
    if loader is not None:
        value = loader()
        _globals[name] = value
        return value
    raise AttributeError(f"module {module!r} has no attribute {name!r}")
