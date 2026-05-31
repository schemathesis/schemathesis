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
def test_stateful_finds_chained_planted_bug(ctx, cli, snapshot_cli, filter_arg):
    # Producer chains to a dependent consumer and exposes a planted error.
    api = ctx.graphql.apps.use_after_create()
    assert (
        cli.run(
            api.schema_url,
            "--no-shrink",
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


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_stateful_finds_non_id_chain_bug(ctx, cli, snapshot_cli):
    # A query seeds Project.fullPath; the consuming mutation errors only on a real captured path.
    api = ctx.graphql.apps.non_id_pool()
    assert (
        cli.run(
            api.schema_url,
            "--no-shrink",
            "--max-examples=30",
            "--phases=stateful",
            "-m",
            "positive",
            "-c",
            "not_a_server_error",
        )
        == snapshot_cli
    )


@pytest.mark.parametrize(
    "make_api",
    [lambda apps: apps.bare_slug(), lambda apps: apps.relay_connection()],
    ids=["bare-slug-arg", "relay-connection-producer"],
)
@pytest.mark.snapshot(replace_reproduce_with=True)
def test_stateful_finds_non_id_lookup_bug(ctx, cli, snapshot_cli, make_api):
    # Bare `slug` resolves via the enclosing return type; the producer may be a Relay connection.
    api = make_api(ctx.graphql.apps)
    assert (
        cli.run(
            api.schema_url,
            "--no-shrink",
            "--max-examples=30",
            "--phases=stateful",
            "-m",
            "positive",
            "-c",
            "not_a_server_error",
        )
        == snapshot_cli
    )


def _use_after_delete(apps):
    return apps.use_after_delete()


def _double_delete(apps):
    return apps.double_delete()


@pytest.mark.parametrize(
    "make_api",
    [_use_after_delete, _double_delete],
    ids=["use-after-delete", "double-delete"],
)
@pytest.mark.snapshot(replace_reproduce_with=True)
def test_stateful_finds_tombstone_bugs(ctx, cli, snapshot_cli, make_api):
    # Tombstones expose use-after-delete and double-delete on a known-deleted resource.
    # Tombstone probes need many iterations to traverse the deleted-id bundle reliably.
    api = make_api(ctx.graphql.apps)
    assert (
        cli.run(
            api.schema_url,
            "--no-shrink",
            "--max-examples=75",
            "--phases=stateful",
            "-m",
            "positive",
            "-c",
            "not_a_server_error",
        )
        == snapshot_cli
    )
