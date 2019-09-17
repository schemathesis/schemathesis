from typing import IO, Any, Dict, Union
from urllib.request import urlopen

import yaml

from .schemas import BaseSchema, OpenApi30, SwaggerV20
from .types import PathLike
from .utils import deprecated


def from_path(path: PathLike) -> BaseSchema:
    """Load a file from OS path and parse to schema instance.."""
    with open(path) as fd:
        return from_file(fd)


def from_uri(uri: str) -> BaseSchema:
    """Load a remote resource and parse to schema instance."""
    response = urlopen(uri)
    data = response.read()
    return from_file(data)


def from_file(file: Union[IO[str], str]) -> BaseSchema:
    """Load a file content and parse to schema instance.

    `file` could be a file descriptor, string or bytes.
    """
    raw = yaml.safe_load(file)
    return from_dict(raw)


def from_dict(raw_schema: Dict[str, Any]) -> BaseSchema:
    """Get a proper abstraction for the given raw schema."""
    if "swagger" in raw_schema:
        return SwaggerV20(raw_schema)
    if "openapi" in raw_schema:
        return OpenApi30(raw_schema)
    raise ValueError("Unsupported schema type")


# Backward compatibility
class Parametrizer:
    from_path = deprecated(from_path, "`Parametrizer.from_path` is deprecated, use `schemathesis.from_path` instead.")
    from_uri = deprecated(from_uri, "`Parametrizer.from_uri` is deprecated, use `schemathesis.from_uri` instead.")
