from datetime import timedelta

from schemathesis.parametrizer import SchemaParametrizer, is_schemathesis_test


def test_raw_schema():
    # When schema is passed as a dictionary directly
    # Then it should be used for schema wrapper
    assert SchemaParametrizer({}).schema.raw_schema == {}


def test_lazy_callable():
    # When schema is passed as a callable
    # Then the evaluation result should be used for schema wrapper
    assert SchemaParametrizer(lambda: {}).schema.raw_schema == {}


def test_parametrize_hypothesis_settings():
    # When parametrizer already have some hypothesis-related attributes
    parametrizer = SchemaParametrizer({}, max_examples=10)

    @parametrizer.parametrize()
    def test_():
        pass

    # Then they should be in the parametrized test as well
    assert test_._schema_parametrizer.hypothesis_settings == {"max_examples": 10}


def test_parametrize_extend_hypothesis_settings():
    # When parametrizer already have some hypothesis-related attributes
    parametrizer = SchemaParametrizer({}, max_examples=10)

    @parametrizer.parametrize(deadline=timedelta(seconds=1))
    def test_():
        pass

    # Then they should be extended with values passed to `parametrize`

    assert test_._schema_parametrizer.hypothesis_settings == {"max_examples": 10, "deadline": timedelta(seconds=1)}


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
        return {}

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
