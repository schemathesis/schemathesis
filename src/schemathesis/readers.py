"""From given input get the raw schema."""
from typing import IO, Union
from urllib.request import urlopen

import yaml

from .types import PathLike, Schema


def from_path(path: PathLike) -> Schema:
    """Load a file from OS path and parse to schema dictionary."""
    with open(path) as fd:
        return from_file(fd)


def from_uri(uri: str) -> Schema:
    """Load a remote resource and parse to schema dictionary."""
    response = urlopen(uri)
    data = response.read()
    return from_file(data)


def from_file(file: Union[IO[str], str]) -> Schema:
    """Load a file content and parse to schema dictionary.

    `file` could be a file descriptor, string or bytes.
    """
    return yaml.safe_load(file)
