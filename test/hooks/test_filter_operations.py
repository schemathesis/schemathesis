import pytest


@pytest.mark.parametrize(
    "schema_fixture",
    ("graphql_schema", "openapi3_schema"),
)
def test_hook(request, schema_fixture):
    schema = request.getfixturevalue(schema_fixture)
    {
        "graphql_schema": assert_graphql,
        "openapi3_schema": assert_openapi,
    }[schema_fixture](schema)


def assert_graphql(schema):
    assert len(list(schema.get_all_operations())) == 4

    @schema.hook
    def filter_operations(context):
        # Skips non queries
        return context.operation.definition.is_query

    for operation in schema.get_all_operations():
        assert not operation.ok().definition.is_mutation


def assert_openapi(schema):
    assert len(list(schema.get_all_operations())) == 2

    @schema.hook
    def filter_operations(context):
        return context.operation.path == "/failure"

    for operation in schema.get_all_operations():
        assert operation.ok().path == "/failure"
