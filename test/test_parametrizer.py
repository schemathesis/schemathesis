import pytest

from schemathesis.parametrizer import Parametrizer, is_schemathesis_test

from .utils import SIMPLE_PATH

MINIMAL_SCHEMA = {"swagger": "2.0"}


@pytest.mark.parametrize("schema", (MINIMAL_SCHEMA, lambda: MINIMAL_SCHEMA), ids=("raw_schema", "lazy_callable"))
def test_raw_schema(schema):
    # When schema is passed as a dictionary or a callable
    # Then it should be used for schema wrapper
    assert Parametrizer(schema).raw_schema.get() == MINIMAL_SCHEMA


@pytest.mark.parametrize(
    "method, path", ((Parametrizer.from_path, SIMPLE_PATH), (Parametrizer.from_uri, f"file://{SIMPLE_PATH}"))
)
def test_alternative_constructors(simple_schema, method, path):
    assert method(path).raw_schema.get() == simple_schema


def test_is_schemathesis_test():
    # When a test is wrapped into `Parametrizer.parametrize`
    parametrizer = Parametrizer({})

    @parametrizer.parametrize()
    def test_a():
        pass

    # Then is should be recognized as a schemathesis test
    assert is_schemathesis_test(test_a)


def test_callable_schema_cache():
    # When a parametrized is created with a callable
    counter = 0

    def load_schema():
        nonlocal counter
        counter += 1
        return MINIMAL_SCHEMA

    parametrizer = Parametrizer(load_schema)

    @parametrizer.parametrize()
    def test_a():
        pass

    @parametrizer.parametrize()
    def test_b():
        pass

    assert test_a._schema_parametrizer.schema == test_b._schema_parametrizer.schema

    # Then this callable should be evaluated only once and reused
    assert counter == 1
