"""Work with stored auth data."""
import enum
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

import attr
import tomli
import tomli_w

from ..types import PathLike
from .constants import DEFAULT_HOSTNAME, DEFAULT_HOSTS_PATH, HOSTS_FORMAT_VERSION


@attr.s(slots=True)
class HostData:
    """Stored data related to a host."""

    hostname: str = attr.ib()
    hosts_file: PathLike = attr.ib()

    def load(self) -> Dict[str, Any]:
        return load(self.hosts_file).get(self.hostname, {})

    @property
    def correlation_id(self) -> Optional[str]:
        return self.load().get("correlation_id")

    def store_correlation_id(self, correlation_id: str) -> None:
        """Store `correlation_id` in the hosts file."""
        hosts = load(self.hosts_file)
        data = hosts.setdefault(self.hostname, {})
        data["correlation_id"] = correlation_id
        _dump_hosts(self.hosts_file, hosts)


def store(token: str, hostname: str = DEFAULT_HOSTNAME, hosts_file: PathLike = DEFAULT_HOSTS_PATH) -> None:
    """Store a new token for a host."""
    # Don't use any file-based locking for simplicity
    hosts = load(hosts_file)
    data = hosts.setdefault(hostname, {})
    data.update(version=HOSTS_FORMAT_VERSION, token=token)
    _dump_hosts(hosts_file, hosts)


def load(path: PathLike) -> Dict[str, Any]:
    """Load the given hosts file.

    Return an empty dict if it doesn't exist.
    """
    try:
        with open(path, "rb") as fd:
            return tomli.load(fd)
    except FileNotFoundError:
        _try_make_config_directory(path)
        return {}
    except tomli.TOMLDecodeError:
        return {}


def _try_make_config_directory(path: PathLike) -> None:
    # Try to create the parent dir - it could be the first run, when the config dir doesn't exist yet
    try:
        Path(path).parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    except OSError:
        # Ignore permission errors, etc
        pass


def load_for_host(hostname: str = DEFAULT_HOSTNAME, hosts_file: PathLike = DEFAULT_HOSTS_PATH) -> Dict[str, Any]:
    """Load all data associated with a hostname."""
    return load(hosts_file).get(hostname, {})


@enum.unique
class RemoveAuth(enum.Enum):
    success = 1
    no_match = 2
    no_hosts = 3
    error = 4


def remove(hostname: str = DEFAULT_HOSTNAME, hosts_file: PathLike = DEFAULT_HOSTS_PATH) -> RemoveAuth:
    """Remove authentication for a Schemathesis.io host."""
    try:
        with open(hosts_file, "rb") as fd:
            hosts = tomli.load(fd)
        try:
            hosts.pop(hostname)
            _dump_hosts(hosts_file, hosts)
            return RemoveAuth.success
        except KeyError:
            return RemoveAuth.no_match
    except FileNotFoundError:
        return RemoveAuth.no_hosts
    except tomli.TOMLDecodeError:
        return RemoveAuth.error


def get_token(hostname: str = DEFAULT_HOSTNAME, hosts_file: PathLike = DEFAULT_HOSTS_PATH) -> Optional[str]:
    """Load a token for a host."""
    return load_for_host(hostname, hosts_file).get("token")


def get_temporary_hosts_file() -> str:
    temporary_dir = Path(tempfile.gettempdir()).resolve()
    return str(temporary_dir / "schemathesis-hosts.toml")


def _dump_hosts(path: PathLike, hosts: Dict[str, Any]) -> None:
    """Write hosts data to a file."""
    with open(path, "wb") as fd:
        tomli_w.dump(hosts, fd)
