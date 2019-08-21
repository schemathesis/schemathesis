from datetime import timedelta

import pytest

from schemathesis.parametrizer import SchemaParametrizer, is_schemathesis_test

MINIMAL_SCHEMA = {"swagger": "2.0"}


@pytest.mark.parametrize("schema", (MINIMAL_SCHEMA, lambda: MINIMAL_SCHEMA), ids=("raw_schema", "lazy_callable"))
def test_raw_schema(schema):
    # When schema is passed as a dictionary or a callable
    # Then it should be used for schema wrapper
    assert SchemaParametrizer(schema).schema.raw_schema == MINIMAL_SCHEMA


def test_parametrize_hypothesis_settings():
    # When parametrizer already have some hypothesis-related attributes
    parametrizer = SchemaParametrizer({}, max_examples=10)

    @parametrizer.parametrize()
    def test():
        pass

    # Then they should be in the parametrized test as well
    assert test._schema_parametrizer.hypothesis_settings == {"max_examples": 10}


def test_parametrize_extend_hypothesis_settings():
    # When parametrizer already have some hypothesis-related attributes
    parametrizer = SchemaParametrizer({}, max_examples=10)

    @parametrizer.parametrize(deadline=timedelta(seconds=1))
    def test():
        pass

    # Then they should be extended with values passed to `parametrize`

    assert test._schema_parametrizer.hypothesis_settings == {"max_examples": 10, "deadline": timedelta(seconds=1)}


def test_is_schemathesis_test():
    # When a test is wrapped into `SchemaParametrizer.parametrize`
    parametrizer = SchemaParametrizer({})

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

    parametrizer = SchemaParametrizer(load_schema)

    @parametrizer.parametrize()
    def test_a():
        pass

    @parametrizer.parametrize()
    def test_b():
        pass

    assert test_a._schema_parametrizer.schema == test_b._schema_parametrizer.schema

    # Then this callable should be evaluated only once and reused
    assert counter == 1


# TODO. respect hypothesis-profile
