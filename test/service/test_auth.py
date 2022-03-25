import pytest

from schemathesis.service import TOKEN_ENV_VAR
from schemathesis.service.hosts import get_token


def malform_hosts(path):
    with open(path, "w") as fd:
        fd.write("[[wrong]")


def assert_token(hosts_file, token):
    # And a new file is created
    assert hosts_file.exists()
    # And token could be loaded
    assert get_token(hosts_file=hosts_file) == token


@pytest.mark.parametrize(
    "setup",
    (
        lambda f: None,
        malform_hosts,
    ),
    ids=["nothing", "malformed-file"],
)
def test_explicit_token(cli, hosts_file, setup):
    setup(hosts_file)
    # When the user logs in with a token
    token = "sample_token"
    result = cli.auth.login(token, f"--hosts-file={hosts_file}")
    # Then the command succeeds
    assert result.exit_code == 0, result.stdout
    assert_token(hosts_file, token)


def test_env_var(cli, hosts_file, monkeypatch):
    token = "sample_token"
    monkeypatch.setenv(TOKEN_ENV_VAR, token)
    # When the user logs in with an env var token
    result = cli.auth.login(f"--hosts-file={hosts_file}")
    # Then the command succeeds
    assert result.exit_code == 0, result.stdout
    assert_token(hosts_file, token)


def test_missing_parent_dir(cli, tmp_path):
    # When the config directory does not exist
    # And its parent dir does not exist as well
    hosts_file = tmp_path / "a" / "b" / "hosts.toml"
    token = "sample_token"
    result = cli.auth.login(token, f"--hosts-file={hosts_file}")
    # Then the config file should be created anyway
    assert result.exit_code == 0, result.stdout
    assert_token(hosts_file, token)
