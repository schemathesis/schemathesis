import datetime
import os

import yaml

import schemathesis

HERE = os.path.dirname(os.path.abspath(__file__))


def get_schema_path(schema_name):
    return os.path.join(HERE, "data", schema_name)


SIMPLE_PATH = get_schema_path("simple_swagger.yaml")


def get_schema(schema_name="simple_swagger.yaml", **kwargs):
    schema = make_schema(schema_name, **kwargs)
    return schemathesis.from_dict(schema)


def make_schema(schema_name="simple_swagger.yaml", **kwargs):
    path = get_schema_path(schema_name)
    with open(path) as fd:
        schema = yaml.safe_load(fd)
    if kwargs is not None:
        schema = merge(kwargs, schema)
    return schema


def merge(a, b):
    for key in b:
        if key in a:
            if isinstance(a[key], dict) and isinstance(b[key], dict):
                merge(a[key], b[key])
        else:
            a[key] = b[key]
    return a


def integer(**kwargs):
    return {"type": "integer", "in": "query", **kwargs}


def string(**kwargs):
    return {"type": "string", "in": "query", **kwargs}


def array(**kwargs):
    return {"name": "values", "in": "query", "type": "array", **kwargs}


def as_param(*parameters):
    return {"paths": {"/users": {"get": {"parameters": list(parameters)}}}}


def as_array(**kwargs):
    return as_param(array(**kwargs))


def noop(value):
    return True


def _assert_value(value, type, predicate=noop):
    assert isinstance(value, type)
    assert predicate(value)


def assert_int(value, predicate=noop):
    _assert_value(value, int, predicate)


def assert_str(value, predicate=noop):
    _assert_value(value, str, predicate)


def assert_list(value, predicate=noop):
    _assert_value(value, list, predicate)


def _assert_date(value, format):
    try:
        datetime.datetime.strptime(value, format)
        return True
    except ValueError:
        return False


def assert_date(value):
    return _assert_date(value, "%Y-%m-%d")


def assert_datetime(value):
    return _assert_date(value, "%Y-%m-%dT%H:%M:%S%z") or _assert_date(value, "%Y-%m-%dT%H:%M:%S.%f%z")
