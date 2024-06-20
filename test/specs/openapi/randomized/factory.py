from dataclasses import fields, is_dataclass

from .types import Missing


def _asdict_inner(obj):
    if is_dataclass(obj):
        result = {}
        for f in fields(obj):
            value = getattr(obj, f.name)
            if not isinstance(value, Missing):
                result[f.name] = _asdict_inner(value)
        if hasattr(obj, "map_value"):
            result = obj.map_value(result)
        return result
    elif isinstance(obj, (list, tuple)):
        return type(obj)(_asdict_inner(v) for v in obj)
    elif isinstance(obj, dict):
        return type(obj)((_asdict_inner(k), _asdict_inner(v)) for k, v in obj.items())
    return obj


def asdict(schema):
    return _asdict_inner(schema)
