import datetime
import os
from functools import lru_cache
from typing import Any, Callable, Dict, Type

import yaml

import schemathesis
from schemathesis.schemas import BaseSchema

HERE = os.path.dirname(os.path.abspath(__file__))


def get_schema_path(schema_name: str) -> str:
    return os.path.join(HERE, "data", schema_name)


SIMPLE_PATH = get_schema_path("simple_swagger.yaml")


def get_schema(schema_name: str = "simple_swagger.yaml", **kwargs: Any) -> BaseSchema:
    schema = make_schema(schema_name, **kwargs)
    return schemathesis.from_dict(schema)


def make_schema(schema_name: str = "simple_swagger.yaml", **kwargs: Any) -> Dict[str, Any]:
    schema = load_schema(schema_name)
    if kwargs is not None:
        schema = merge(kwargs, schema)
    return schema


@lru_cache()
def load_schema(schema_name: str) -> Dict[str, Any]:
    path = get_schema_path(schema_name)
    with open(path) as fd:
        return yaml.safe_load(fd)


def merge(a: Dict[str, Any], b: Dict[str, Any]) -> Dict[str, Any]:
    for key in b:
        if key in a:
            if isinstance(a[key], dict) and isinstance(b[key], dict):
                merge(a[key], b[key])
        else:
            a[key] = b[key]
    return a


def integer(**kwargs: Any) -> Dict[str, Any]:
    return {"type": "integer", "in": "query", **kwargs}


def string(**kwargs: Any) -> Dict[str, Any]:
    return {"type": "string", "in": "query", **kwargs}


def array(**kwargs: Any) -> Dict[str, Any]:
    return {"name": "values", "in": "query", "type": "array", **kwargs}


def as_param(*parameters: Any) -> Dict[str, Any]:
    return {"paths": {"/users": {"get": {"parameters": list(parameters)}}}}


def as_array(**kwargs: Any) -> Dict[str, Any]:
    return as_param(array(**kwargs))


def noop(value: Any) -> bool:
    return True


def _assert_value(value: Any, type: Type, predicate: Callable = noop) -> None:
    assert isinstance(value, type)
    assert predicate(value)


def assert_int(value: Any, predicate: Callable = noop) -> None:
    _assert_value(value, int, predicate)


def assert_str(value: Any, predicate: Callable = noop) -> None:
    _assert_value(value, str, predicate)


def assert_bytes(value: Any, predicate: Callable = noop) -> None:
    _assert_value(value, bytes, predicate)


def assert_list(value: Any, predicate: Callable = noop) -> None:
    _assert_value(value, list, predicate)


def _assert_date(value: str, format: str) -> bool:
    try:
        datetime.datetime.strptime(value, format)
        return True
    except ValueError:
        return False


def assert_date(value: str) -> bool:
    return _assert_date(value, "%Y-%m-%d")


def assert_datetime(value: str) -> bool:
    return _assert_date(value, "%Y-%m-%dT%H:%M:%S%z") or _assert_date(value, "%Y-%m-%dT%H:%M:%S.%f%z")
