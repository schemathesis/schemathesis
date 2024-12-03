from __future__ import annotations

from typing import Any

from schemathesis import errors, graphql, openapi, pytest, python
from schemathesis.checks import CheckContext, CheckFunction, check
from schemathesis.core.output import OutputConfig
from schemathesis.core.version import SCHEMATHESIS_VERSION
from schemathesis.generation.targets import TargetContext, TargetFunction, target

from . import auths, contrib, experimental, hooks, runner, serializers
from ._lazy_import import lazy_import
from .generation import DataGenerationMethod, GenerationConfig, HeaderConfig
from .models import Case

__version__ = SCHEMATHESIS_VERSION

# Public API
auth = auths.GLOBAL_AUTH_STORAGE
hook = hooks.register
serializer = serializers.register

__all__ = [
    "auths",
    "check",
    "CheckContext",
    "CheckFunction",
    "errors",
    "experimental",
    "contrib",
    "graphql",
    "openapi",
    "python",
    "pytest",
    "hooks",
    "runner",
    "serializers",
    "target",
    "TargetContext",
    "TargetFunction",
    "DataGenerationMethod",
    "Case",
    "__version__",
    "auth",
    "hook",
    "serializer",
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
