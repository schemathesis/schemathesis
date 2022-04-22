"""A private API to work with Schemathesis internals."""
from .specs.openapi import _hypothesis


def clear_cache() -> None:
    _hypothesis.clear_cache()
