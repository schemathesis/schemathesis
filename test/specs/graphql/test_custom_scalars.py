import pytest
from _pytest.main import ExitCode
from hypothesis import given
from hypothesis import strategies as st

import schemathesis
from schemathesis.core.errors import IncorrectUsage
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
  getByDate(value: Date!): Int!
}
"""
    schema = schemathesis.graphql.from_file(raw_schema)

    @given(schema["Query"]["getByDate"].as_strategy())
    def test(case):
        # Then scalars should be properly generated
        assert f'getByDate(value: "{expected}")' in case.body

    test()


def test_custom_scalar_in_cli(testdir, cli, snapshot_cli, graphql_url):
    schema_file = testdir.make_graphql_schema_file(
        """
scalar FooBar

type Query {
  getByDate(value: FooBar!): Int!
}
    """,
    )
    assert cli.run(str(schema_file), f"--url={graphql_url}") == snapshot_cli


def test_built_in_scalars_in_cli(testdir, cli, graphql_url):
    schema_file = testdir.make_graphql_schema_file(
        """
scalar Date
scalar Time
scalar DateTime
scalar IP
scalar IPv4
scalar IPv6
scalar BigInt
scalar Long
scalar UUID

type Query {
  getByDate(value: Date!): Int!
  getByTime(value: Time!): Int!
  getByDateTime(value: DateTime!): Int!
  getByIP(value: IP!): Int!
  getByIPv4(value: IPv4!): Int!
  getByIPv6(value: IPv6!): Int!
  getByLong(value: Long!): Int!
  getByBigInt(value: BigInt!): Int!
  getByUUID(value: UUID!): Int!
}""",
    )
    result = cli.run_and_assert(
        str(schema_file),
        "--max-examples=5",
        f"--url={graphql_url}",
        exit_code=ExitCode.TESTS_FAILED,
    )
    # Queries can be constructed, but the backend does not implement the fields
    assert result.stdout.count("Cannot query field") == 18


@pytest.mark.parametrize(
    ("name", "value", "expected"),
    [
        (42, st.just("foo").map(nodes.String), "Scalar name 42 must be a string"),
        ("Date", 42, "42 must be a Hypothesis strategy which generates AST nodes matching this scalar"),
    ],
)
def test_invalid_strategy(name, value, expected):
    with pytest.raises(IncorrectUsage, match=expected):
        schemathesis.graphql.scalar(name, value)
