import platform
from pathlib import Path

import pytest

from schemathesis.service import TOKEN_ENV_VAR, hosts
from schemathesis.service.hosts import get_temporary_hosts_file


def malform_hosts(path):
    with open(path, "w") as fd:
        fd.write("[[wrong]")


def assert_token(hostname, hosts_file, token):
    # And a new file is created
    assert hosts_file.exists()
    # And token could be loaded
    assert hosts.get_token(hostname=hostname, hosts_file=hosts_file) == token


USERNAME = "TestUser"
successful_login = pytest.mark.service(data={"username": USERNAME}, status=200, method="POST", path="/auth/cli/login/")


@pytest.mark.parametrize(
    "setup",
    (
        lambda f: None,
        malform_hosts,
    ),
    ids=["nothing", "malformed-file"],
)
@successful_login
def test_explicit_token(cli, hosts_file, hostname, setup, service):
    setup(hosts_file)
    # When the user logs in with a token
    token = "sample_token"
    result = cli.auth.login(token, f"--hosts-file={hosts_file}", f"--hostname={hostname}", "--protocol=http")
    # Then the command succeeds
    assert result.exit_code == 0, result.stdout
    assert result.stdout.strip() == f"✔️ Logged in into {hostname} as {USERNAME}"
    assert_token(hostname, hosts_file, token)


@successful_login
def test_env_var(cli, hostname, service, hosts_file, monkeypatch):
    token = "sample_token"
    monkeypatch.setenv(TOKEN_ENV_VAR, token)
    # When the user logs in with an env var token
    result = cli.auth.login(f"--hosts-file={hosts_file}", f"--hostname={hostname}", "--protocol=http")
    # Then the command succeeds
    assert result.exit_code == 0, result.stdout
    assert_token(hostname, hosts_file, token)


@successful_login
def test_missing_parent_dir(cli, hostname, service, tmp_path):
    # When the config directory does not exist
    # And its parent dir does not exist as well
    hosts_file = tmp_path / "a" / "b" / "hosts.toml"
    token = "sample_token"
    result = cli.auth.login(token, f"--hosts-file={hosts_file}", f"--hostname={hostname}", "--protocol=http")
    # Then the config file should be created anyway
    assert result.exit_code == 0, result.stdout
    assert_token(hostname, hosts_file, token)


@successful_login
@pytest.mark.skipif(platform.system() != "Linux", reason="Linux specific issue")
def test_permission_denied(cli, hostname, service, tmp_path):
    # When the default config directory is not usable
    config_directory = tmp_path / ".config"
    config_directory.mkdir(mode=0o644)
    hosts_file = config_directory / "hosts.toml"
    token = "sample_token"
    result = cli.auth.login(token, f"--hosts-file={hosts_file}", f"--hostname={hostname}", "--protocol=http")
    # Then the config file should be created anyway in a different location
    assert result.exit_code == 0, result.stdout
    # And a warning is emitted
    warning = result.stdout.splitlines()[0]
    assert warning.startswith("WARNING: The provided hosts.toml file location is unusable")
    actual_hosts_file = Path(get_temporary_hosts_file())
    assert_token(hostname, actual_hosts_file, token)


ERROR_MESSAGE = "Invalid credentials"


@pytest.mark.service(data={"detail": ERROR_MESSAGE}, status=401, method="POST", path="/auth/cli/login/")
def test_invalid_auth(cli, hosts_file, hostname, service, tmp_path):
    # When token is invalid
    result = cli.auth.login("sample_token", f"--hosts-file={hosts_file}", f"--hostname={hostname}", "--protocol=http")
    # Then there should be an error with proper error message
    assert result.exit_code == 1, result.stdout
    assert result.stdout.strip() == f"❌ Failed to login into {hostname}: {ERROR_MESSAGE}"
    # And the token should not be saved
    assert not hosts_file.exists()
    assert hosts.get_token(hostname=hostname, hosts_file=hosts_file) is None


def test_logout_success(cli, hosts_file):
    # When CLI is logged in
    hostname = "127.0.0.1"
    hosts.store("foo", hostname=hostname, hosts_file=hosts_file)
    result = cli.auth.logout(f"--hosts-file={hosts_file}", f"--hostname={hostname}")
    # Then it should be a success message
    assert result.exit_code == 0, result.stdout
    assert result.stdout.strip() == f"✔️ Logged out of {hostname} account"
    # And the auth info should be removed from the hosts file
    data = hosts.load(hosts_file)
    assert hostname not in data


def test_logout_no_hosts_info(cli, hosts_file):
    # When there is no hosts info
    result = cli.auth.logout(f"--hosts-file={hosts_file}")
    # Then it should be an error on logout
    assert result.exit_code == 1, result.stdout
    assert result.stdout.strip() == "🟡️ Not logged in to any hosts"
    # And the hosts file should not be created
    assert not hosts_file.exists()


def test_logout_no_specific_host_info(cli, hosts_file):
    # When there is no hosts info
    hostname = "127.0.0.1"
    hosts.store("foo", hostname="127.0.0.2", hosts_file=hosts_file)
    result = cli.auth.logout(f"--hosts-file={hosts_file}", f"--hostname={hostname}")
    # Then it should be an error on logout
    assert result.exit_code == 1, result.stdout
    assert result.stdout.strip() == "🟡️ Not logged in to 127.0.0.1"


def test_logout_malformed_hosts_file(cli, hosts_file):
    # When the hosts file is malformed
    malform_hosts(hosts_file)
    result = cli.auth.logout(f"--hosts-file={hosts_file}")
    # Then it should be an error on logout
    assert result.exit_code == 1, result.stdout
    assert result.stdout.strip() == f"❌ Failed to read the hosts file. Try to remove {hosts_file}"
