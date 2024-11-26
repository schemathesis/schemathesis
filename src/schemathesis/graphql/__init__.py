from schemathesis.graphql.loaders import from_asgi, from_dict, from_file, from_path, from_url, from_wsgi

from ..specs.graphql import nodes
from ..specs.graphql.scalars import scalar

__all__ = [
    "from_url",
    "from_asgi",
    "from_wsgi",
    "from_file",
    "from_path",
    "from_dict",
    "nodes",
    "scalar",
]
