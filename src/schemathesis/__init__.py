from __future__ import annotations

from typing import Any

from schemathesis.core.version import SCHEMATHESIS_VERSION

from . import auths, checks, contrib, experimental, graphql, hooks, runner, serializers, targets
from ._lazy_import import lazy_import
from .generation import DataGenerationMethod, GenerationConfig, HeaderConfig
from .models import Case
from .specs import openapi

__version__ = SCHEMATHESIS_VERSION

# Default loaders
from_asgi = openapi.from_asgi
from_dict = openapi.from_dict
from_file = openapi.from_file
from_path = openapi.from_path
from_pytest_fixture = openapi.from_pytest_fixture
from_uri = openapi.from_uri
from_wsgi = openapi.from_wsgi

# Public API
auth = auths.GLOBAL_AUTH_STORAGE
check = checks.register
hook = hooks.register
serializer = serializers.register
target = targets.register

__all__ = [
    "auths",
    "checks",
    "experimental",
    "contrib",
    "graphql",
    "hooks",
    "runner",
    "serializers",
    "targets",
    "DataGenerationMethod",
    "Case",
    "openapi",
    "__version__",
    "from_asgi",
    "from_dict",
    "from_file",
    "from_path",
    "from_pytest_fixture",
    "from_uri",
    "from_wsgi",
    "auth",
    "check",
    "hook",
    "serializer",
    "target",
    "GenerationConfig",
    "HeaderConfig",
]


def _load_generic_response() -> Any:
    from .transports.responses import GenericResponse

    return GenericResponse


_imports = {"GenericResponse": _load_generic_response}


def __getattr__(name: str) -> Any:
    # Some modules are relatively heavy, hence load them lazily to improve startup time for CLI
    return lazy_import(__name__, name, _imports, globals())
