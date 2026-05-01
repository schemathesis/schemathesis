from __future__ import annotations

import pytest
from hypothesis import strategies as st

import schemathesis
from schemathesis.graphql import nodes
from schemathesis.specs.graphql.scalars import CUSTOM_SCALARS


@pytest.fixture(autouse=True)
def _register_book_id_scalar():
    schemathesis.graphql.scalar("BookID", st.uuids().map(str).map(nodes.String))
    yield
    CUSTOM_SCALARS.clear()


_DEFAULT_CONFIG: dict = {}
_POOL_DISABLED_CONFIG = {"phases": {"fuzzing": {"extra-data-sources": {"responses": False}}}}


@pytest.mark.parametrize(
    ("filter_arg", "config"),
    [
        ("--exclude-name=Mutation.updateBook", _DEFAULT_CONFIG),
        ("--exclude-name=Query.book", _DEFAULT_CONFIG),
        ("--include-name=Query.book", _DEFAULT_CONFIG),
        ("--exclude-name=Mutation.updateBook", _POOL_DISABLED_CONFIG),
    ],
    ids=["use-after-create", "update-on-existing", "no-producer", "pool-disabled-via-config"],
)
@pytest.mark.snapshot(replace_reproduce_with=True)
def test_planted_bug_findability(cli, buggy_graphql_url, snapshot_cli, filter_arg, config):
    assert (
        cli.run(buggy_graphql_url, "--max-examples=10", "-c", "not_a_server_error", filter_arg, config=config)
        == snapshot_cli
    )
