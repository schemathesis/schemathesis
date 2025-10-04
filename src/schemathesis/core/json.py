import platform
from json import JSONDecodeError as JSONDecodeError

if platform.python_implementation() == "PyPy":
    from json import dumps as _dumps
    from json import loads as loads

    def dumps(obj: object) -> str:
        return _dumps(obj)
else:
    import orjson

    def dumps(obj: object) -> str:
        return orjson.dumps(obj).decode("utf-8")

    loads = orjson.loads
