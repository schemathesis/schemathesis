"""Work with stored auth data."""

from __future__ import annotations

import enum
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import tomli
import tomli_w

from schemathesis.core.fs import ensure_parent

from .constants import DEFAULT_HOSTNAME, DEFAULT_HOSTS_PATH, HOSTS_FORMAT_VERSION

if TYPE_CHECKING:
    pass


@dataclass
class HostData:
    """Stored data related to a host."""

    hostname: str
    hosts_file: os.PathLike

    def load(self) -> dict[str, Any]:
        return load(self.hosts_file).get(self.hostname, {})

    @property
    def correlation_id(self) -> str | None:
        return self.load().get("correlation_id")

    def store_correlation_id(self, correlation_id: str) -> None:
        """Store `correlation_id` in the hosts file."""
        hosts = load(self.hosts_file)
        data = hosts.setdefault(self.hostname, {})
        data["correlation_id"] = correlation_id
        _dump_hosts(self.hosts_file, hosts)


def store(token: str, hostname: str = DEFAULT_HOSTNAME, hosts_file: os.PathLike = DEFAULT_HOSTS_PATH) -> None:
    """Store a new token for a host."""
    # Don't use any file-based locking for simplicity
    hosts = load(hosts_file)
    data = hosts.setdefault(hostname, {})
    data.update(version=HOSTS_FORMAT_VERSION, token=token)
    _dump_hosts(hosts_file, hosts)


def load(path: os.PathLike) -> dict[str, Any]:
    """Load the given hosts file.

    Return an empty dict if it doesn't exist.
    """
    try:
        with open(path, "rb") as fd:
            return tomli.load(fd)
    except FileNotFoundError:
        ensure_parent(path)
        return {}
    except tomli.TOMLDecodeError:
        return {}


def load_for_host(hostname: str = DEFAULT_HOSTNAME, hosts_file: os.PathLike = DEFAULT_HOSTS_PATH) -> dict[str, Any]:
    """Load all data associated with a hostname."""
    return load(hosts_file).get(hostname, {})


@enum.unique
class RemoveAuth(enum.Enum):
    SUCCESS = 1
    NO_MATCH = 2
    NO_HOSTS = 3
    ERROR = 4


def remove(hostname: str = DEFAULT_HOSTNAME, hosts_file: os.PathLike = DEFAULT_HOSTS_PATH) -> RemoveAuth:
    """Remove authentication for a Schemathesis.io host."""
    try:
        with open(hosts_file, "rb") as fd:
            hosts = tomli.load(fd)
        try:
            hosts.pop(hostname)
            _dump_hosts(hosts_file, hosts)
            return RemoveAuth.SUCCESS
        except KeyError:
            return RemoveAuth.NO_MATCH
    except FileNotFoundError:
        return RemoveAuth.NO_HOSTS
    except tomli.TOMLDecodeError:
        return RemoveAuth.ERROR


def get_token(hostname: str = DEFAULT_HOSTNAME, hosts_file: os.PathLike = DEFAULT_HOSTS_PATH) -> str | None:
    """Load a token for a host."""
    return load_for_host(hostname, hosts_file).get("token")


def get_temporary_hosts_file() -> str:
    temporary_dir = Path(tempfile.gettempdir()).resolve()
    return str(temporary_dir / "schemathesis-hosts.toml")


def _dump_hosts(path: os.PathLike, hosts: dict[str, Any]) -> None:
    """Write hosts data to a file."""
    with open(path, "wb") as fd:
        tomli_w.dump(hosts, fd)
