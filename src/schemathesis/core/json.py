import platform
from json import JSONDecodeError as JSONDecodeError

if platform.python_implementation() == "PyPy":
    from json import dumps as _dumps
    from json import loads as loads

    def dumps(obj: object, *, sort_keys: bool = False) -> str:
        return _dumps(obj, sort_keys=sort_keys)
else:
    import orjson

    def dumps(obj: object, *, sort_keys: bool = False) -> str:
        option = None
        if sort_keys:
            option = orjson.OPT_SORT_KEYS
        return orjson.dumps(obj, option=option).decode("utf-8")

    loads = orjson.loads
