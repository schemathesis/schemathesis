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
def test_planted_bug_findability(ctx, cli, snapshot_cli, filter_arg, config):
    # Positive-only: negative queries fail server validation before capture, so they never exercise the pool.
    api = ctx.graphql.apps.use_after_create()
    assert (
        cli.run(
            api.schema_url,
            "--no-shrink",
            "--max-examples=10",
            "-m",
            "positive",
            "-c",
            "not_a_server_error",
            filter_arg,
            config=config,
        )
        == snapshot_cli
    )


@pytest.mark.parametrize(
    ("filter_args", "config"),
    [
        (("--include-name=Mutation.addUser", "--include-name=Query.user"), _DEFAULT_CONFIG),
        (("--include-name=Mutation.addAuthor", "--include-name=Query.postsByAuthor"), _DEFAULT_CONFIG),
        (("--include-name=Mutation.addUser", "--include-name=Query.user"), _POOL_DISABLED_CONFIG),
    ],
    ids=["bare-id-via-return-type", "arg-name-token", "pool-disabled-via-config"],
)
@pytest.mark.snapshot(replace_reproduce_with=True)
def test_generic_id_planted_bug_findability(ctx, cli, snapshot_cli, filter_args, config):
    api = ctx.graphql.apps.generic_id_pool()
    assert (
        cli.run(
            api.schema_url,
            "--no-shrink",
            "--max-examples=10",
            "-m",
            "positive",
            "-c",
            "not_a_server_error",
            *filter_args,
            config=config,
        )
        == snapshot_cli
    )


@pytest.mark.parametrize(
    "config",
    [_DEFAULT_CONFIG, _POOL_DISABLED_CONFIG],
    ids=["pool-enabled", "pool-disabled-via-config"],
)
@pytest.mark.snapshot(replace_reproduce_with=True)
def test_input_object_planted_bug_findability(ctx, cli, snapshot_cli, config):
    api = ctx.graphql.apps.input_object_pool()
    assert (
        cli.run(
            api.schema_url,
            "--no-shrink",
            "--max-examples=10",
            "-m",
            "positive",
            "-c",
            "not_a_server_error",
            config=config,
        )
        == snapshot_cli
    )


@pytest.mark.parametrize(
    "config",
    [_DEFAULT_CONFIG, _POOL_DISABLED_CONFIG],
    ids=["pool-enabled", "pool-disabled-via-config"],
)
@pytest.mark.snapshot(replace_reproduce_with=True)
def test_list_argument_planted_bug_findability(ctx, cli, snapshot_cli, config):
    api = ctx.graphql.apps.list_argument_pool()
    assert (
        cli.run(
            api.schema_url,
            "--no-shrink",
            "--max-examples=10",
            "-m",
            "positive",
            "-c",
            "not_a_server_error",
            config=config,
        )
        == snapshot_cli
    )


@pytest.mark.parametrize(
    "config",
    [_DEFAULT_CONFIG, _POOL_DISABLED_CONFIG],
    ids=["pool-enabled", "pool-disabled-via-config"],
)
@pytest.mark.snapshot(replace_reproduce_with=True)
def test_tombstone_planted_bug_findability(ctx, cli, snapshot_cli, config):
    # Tombstones evict deleted ids so updateBook draws from still-existing books and finds the planted bug.
    api = ctx.graphql.apps.tombstone_pool()
    assert (
        cli.run(
            api.schema_url,
            "--no-shrink",
            "--max-examples=200",
            "-m",
            "positive",
            "-c",
            "not_a_server_error",
            config=config,
        )
        == snapshot_cli
    )
