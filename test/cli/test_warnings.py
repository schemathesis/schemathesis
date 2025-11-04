import pytest
from _pytest.main import ExitCode


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("basic")
def test_missing_auth_warning_with_fail_on_true(cli, schema_url, tmp_path, monkeypatch):
    # Given a config file that fails on all warnings
    config_file = tmp_path / "schemathesis.toml"
    config_file.write_text("""
[warnings]
fail-on = true
""")
    monkeypatch.chdir(tmp_path)

    # When running tests without proper auth (will trigger missing_auth warning)
    result = cli.run_and_assert(schema_url, exit_code=ExitCode.TESTS_FAILED)
    # And warnings should be displayed
    assert "WARNINGS" in result.stdout
    assert "Authentication failed" in result.stdout


@pytest.mark.openapi_version("3.0")
@pytest.mark.operations("basic")
def test_missing_auth_warning_with_fail_on_specific(cli, schema_url, tmp_path, monkeypatch):
    # Given a config file that fails only on missing_auth warnings
    config_file = tmp_path / "schemathesis.toml"
    config_file.write_text("""
[warnings]
fail-on = ["missing_auth"]
""")
    monkeypatch.chdir(tmp_path)

    # When running tests without proper auth (will trigger missing_auth warning)
    result = cli.run_and_assert(schema_url, exit_code=ExitCode.TESTS_FAILED)
    # And warnings should be displayed
    assert "WARNINGS" in result.stdout
    assert "Authentication failed" in result.stdout
