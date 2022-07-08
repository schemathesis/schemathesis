import pytest

from schemathesis.service import hosts


def test_hosts_does_not_exist(tmp_path):
    hosts_file = tmp_path / ".config" / "schemathesis" / "hosts.toml"
    hosts._dump_hosts(hosts_file, {})


def test_hosts_does_not_exist_propagated(mocker, tmp_path):
    mocker.patch.object(hosts.Path, "mkdir", side_effect=OSError)
    hosts_file = tmp_path / ".config" / "schemathesis" / "hosts.toml"
    with pytest.raises(FileNotFoundError):
        hosts._dump_hosts(hosts_file, {})
