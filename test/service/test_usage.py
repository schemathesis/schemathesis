import click
import pytest
from click.testing import CliRunner

import schemathesis
from schemathesis.service import usage

SCHEMA = "http://127.0.0.1:/schema.json"


@pytest.mark.parametrize(
    "args, expected",
    (
        ([SCHEMA], {"schema_kind": "URL", "parameters": {}, "used_headers": []}),
        (
            [SCHEMA, "-H", "Authorization:key", "-H", "X-Key:value"],
            {"schema_kind": "URL", "parameters": {}, "used_headers": ["Authorization", "X-Key"]},
        ),
        (
            [SCHEMA, "--hypothesis-max-examples=10"],
            {
                "schema_kind": "URL",
                "parameters": {"hypothesis_max_examples": {"value": "10"}},
                "used_headers": [],
            },
        ),
        (
            [SCHEMA, "--hypothesis-phases=generate"],
            {
                "schema_kind": "URL",
                "parameters": {"hypothesis_phases": {"value": "generate"}},
                "used_headers": [],
            },
        ),
        (
            [SCHEMA, "--checks=not_a_server_error", "--checks=response_conformance"],
            {
                "schema_kind": "URL",
                "parameters": {"checks": {"value": ["not_a_server_error", "response_conformance"]}},
                "used_headers": [],
            },
        ),
        (
            [SCHEMA, "--auth-type=digest", "--auth=user:pass"],
            {
                "schema_kind": "URL",
                "parameters": {"auth_type": {"value": "digest"}, "auth": {"count": 1}},
                "used_headers": [],
            },
        ),
        (
            [SCHEMA, "-E", "a", "-E", "b"],
            {"schema_kind": "URL", "parameters": {"endpoints": {"count": 2}}, "used_headers": []},
        ),
    ),
)
def test_collect(args, expected):
    cli_runner = CliRunner()

    @click.command()
    def run() -> None:
        collected = usage.collect(args)
        # Hooks are global, and could be modified by other tests.
        # Drop them, as it is simpler than introducing locking
        if collected is not None:
            del collected["hooks"]
        assert collected == expected

    result = cli_runner.invoke(run)
    assert result.exit_code == 0, result.exception


def test_collect_out_of_cli_context():
    assert usage.collect() is None


def test_collect_hooks():
    cli_runner = CliRunner()

    @schemathesis.hook
    def before_generate_query(context, strategy):
        # This is noop
        return strategy

    @click.command()
    def run() -> None:
        assert usage.collect([SCHEMA]) == {
            "parameters": {},
            "schema_kind": "URL",
            "used_headers": [],
            "hooks": {"before_generate_query": 1},
        }

    result = cli_runner.invoke(run)
    assert result.exit_code == 0, result.exception
