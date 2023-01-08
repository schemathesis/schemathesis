import pytest
from hypothesis import given
from hypothesis import strategies as st

import schemathesis
from schemathesis.exceptions import UsageError
from schemathesis.graphql import nodes
from schemathesis.specs.graphql.scalars import CUSTOM_SCALARS


@pytest.fixture(autouse=True)
def clear_custom_scalars():
    yield
    CUSTOM_SCALARS.clear()


def test_custom_scalar_graphql():
    # When a custom scalar strategy is registered
    expected = "2022-04-27"
    schemathesis.graphql.scalar("Date", st.just(expected).map(nodes.String))
    raw_schema = """
scalar Date

type Query {
  getByDate(created: Date!): Int!
}
"""
    schema = schemathesis.graphql.from_file(raw_schema)

    @given(schema[b""]["POST"].as_strategy())
    def test(case):
        # Then scalars should be properly generated
        assert f'getByDate(created: "{expected}")' in case.body

    test()


@pytest.mark.parametrize(
    "name, value, expected",
    (
        (42, st.just("foo").map(nodes.String), "Scalar name 42 must be a string"),
        ("Date", 42, "42 must be a Hypothesis strategy which generates AST nodes matching this scalar"),
    ),
)
def test_invalid_strategy(name, value, expected):
    with pytest.raises(UsageError, match=expected):
        schemathesis.graphql.scalar(name, value)
