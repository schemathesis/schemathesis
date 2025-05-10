import textwrap

import pytest


@pytest.mark.parametrize(
    "config_content",
    [
        pytest.param(
            # Syntactically incorrect TOML (missing '=' and comma issues)
            "color true\nsuppress-health-check = [data_too_large,]",
            id="syntactic_error",
        ),
        pytest.param(
            # Valid TOML but contains an unknown top-level property ("unknown_key")
            textwrap.dedent(
                """
                unknown_key = true
                color = false
                """
            ),
            id="unknown_property",
        ),
        pytest.param(
            # Valid TOML but an operations entry is invalid because it does not include any required include/exclude property
            textwrap.dedent(
                """
                [[operations]]
                # Empty operation table: no include or exclude property provided.
                """
            ),
            id="operation_missing_includes",
        ),
        pytest.param(
            # Duplicated filter
            textwrap.dedent(
                """
                [[operations]]
                include-name = "GET /users/"
                exclude-name = "GET /users/"
                """
            ),
            id="operation_duplicate_filter",
        ),
        pytest.param(
            # Invalid expression
            textwrap.dedent(
                """
                [[operations]]
                include-by = "incorrect"
                """
            ),
            id="operation_invalid_expression",
        ),
        pytest.param(
            # Invalid regex
            textwrap.dedent(
                """
                [[operations]]
                include-name-regex = "[0-"
                """
            ),
            id="operation_invalid_regex",
        ),
        pytest.param(
            # NULL byte
            textwrap.dedent(
                """
                [reports]
                har.path = "\x00"
                """
            ),
            id="null_byte",
        ),
    ],
)
def test_incorrect_config(cli, snapshot_cli, tmp_path, config_content):
    config_file = tmp_path / "config.toml"
    config_file.write_text(textwrap.dedent(config_content))
    result = cli.main(f"--config-file={config_file}", "run", "http://127.0.0.1")
    if result.exception and not isinstance(result.exception, SystemExit):
        raise result.exception
    assert result == snapshot_cli
