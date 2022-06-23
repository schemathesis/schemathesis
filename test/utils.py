import os
import platform
from functools import lru_cache
from typing import Any, Callable, Dict, Type

import click
import pytest
import requests
import urllib3
import yaml

import schemathesis
from schemathesis import Case
from schemathesis.schemas import BaseSchema
from schemathesis.utils import StringDatesYAMLLoader, merge

HERE = os.path.dirname(os.path.abspath(__file__))


def get_schema_path(schema_name: str) -> str:
    return os.path.join(HERE, "data", schema_name)


SIMPLE_PATH = get_schema_path("simple_swagger.yaml")


def get_schema(schema_name: str = "simple_swagger.yaml", **kwargs: Any) -> BaseSchema:
    schema = make_schema(schema_name, **kwargs)
    return schemathesis.from_dict(schema)


def make_schema(schema_name: str = "simple_swagger.yaml", **kwargs: Any) -> Dict[str, Any]:
    schema = load_schema(schema_name)
    return merge(kwargs, schema)


@lru_cache()
def load_schema(schema_name: str) -> Dict[str, Any]:
    path = get_schema_path(schema_name)
    with open(path) as fd:
        return yaml.load(fd, StringDatesYAMLLoader)


def integer(**kwargs: Any) -> Dict[str, Any]:
    return {"type": "integer", "in": "query", **kwargs}


def as_param(*parameters: Any) -> Dict[str, Any]:
    return {"paths": {"/users": {"get": {"parameters": list(parameters), "responses": {"200": {"description": "OK"}}}}}}


def noop(value: Any) -> bool:
    return True


def _assert_value(value: Any, type: Type, predicate: Callable = noop) -> None:
    assert isinstance(value, type)
    assert predicate(value)


def assert_int(value: Any, predicate: Callable = noop) -> None:
    _assert_value(value, int, predicate)


def assert_str(value: Any, predicate: Callable = noop) -> None:
    _assert_value(value, str, predicate)


def assert_list(value: Any, predicate: Callable = noop) -> None:
    _assert_value(value, list, predicate)


def assert_requests_call(case: Case):
    """Verify that all generated input parameters are usable by requests."""
    with pytest.raises((requests.exceptions.ConnectionError, urllib3.exceptions.NewConnectionError)):
        case.call(base_url="http://127.0.0.1:1")


def strip_style_win32(styled_output: str) -> str:
    """Strip text style on Windows.

    `click.style` produces ANSI sequences, however they were not supported
    by PowerShell until recently and colored output is created differently.
    """
    if platform.system() == "Windows":
        return click.unstyle(styled_output)
    return styled_output
