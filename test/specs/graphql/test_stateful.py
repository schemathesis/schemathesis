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


@pytest.mark.parametrize(
    "filter_arg",
    [
        "--exclude-name=Mutation.updateBook",
        "--exclude-name=Query.book",
    ],
    ids=["use-after-create", "update-on-existing"],
)
@pytest.mark.snapshot(replace_reproduce_with=True)
def test_stateful_finds_chained_planted_bug(cli, buggy_graphql_url, snapshot_cli, filter_arg):
    # Producer chains to a dependent consumer and exposes a planted error.
    assert (
        cli.run(
            buggy_graphql_url,
            "--max-examples=20",
            "--phases=stateful",
            "-m",
            "positive",
            "-c",
            "not_a_server_error",
            filter_arg,
        )
        == snapshot_cli
    )


@pytest.mark.parametrize(
    "url_fixture",
    ["buggy_stateful_use_after_delete_url", "buggy_stateful_double_delete_url"],
    ids=["use-after-delete", "double-delete"],
)
@pytest.mark.snapshot(replace_reproduce_with=True)
def test_stateful_finds_tombstone_bugs(cli, request, snapshot_cli, url_fixture):
    # Tombstones expose use-after-delete and double-delete on a known-deleted resource.
    # Tombstone probes need many iterations to traverse the deleted-id bundle reliably.
    url = request.getfixturevalue(url_fixture)
    assert (
        cli.run(
            url,
            "--max-examples=75",
            "--phases=stateful",
            "-m",
            "positive",
            "-c",
            "not_a_server_error",
        )
        == snapshot_cli
    )
