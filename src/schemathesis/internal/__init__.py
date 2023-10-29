"""A private API to work with Schemathesis internals."""


def clear_cache() -> None:
    from ..specs.openapi import _hypothesis

    _hypothesis.clear_cache()
