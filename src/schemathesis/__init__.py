from __future__ import annotations

from typing import Any

from schemathesis import graphql, openapi, pytest, python
from schemathesis.core.version import SCHEMATHESIS_VERSION
from schemathesis.internal.output import OutputConfig

from . import auths, checks, contrib, experimental, hooks, runner, serializers, targets
from ._lazy_import import lazy_import
from .generation import DataGenerationMethod, GenerationConfig, HeaderConfig
from .models import Case

__version__ = SCHEMATHESIS_VERSION

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
    "openapi",
    "python",
    "pytest",
    "hooks",
    "runner",
    "serializers",
    "targets",
    "DataGenerationMethod",
    "Case",
    "openapi",
    "__version__",
    "auth",
    "check",
    "hook",
    "serializer",
    "target",
    "OutputConfig",
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
