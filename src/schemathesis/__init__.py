from __future__ import annotations

from schemathesis import errors, graphql, openapi, pytest, python
from schemathesis.checks import CheckContext, CheckFunction, check
from schemathesis.core.output import OutputConfig, sanitization
from schemathesis.core.transport import Response
from schemathesis.core.version import SCHEMATHESIS_VERSION
from schemathesis.generation.targets import TargetContext, TargetFunction, target

from . import auths, contrib, experimental, hooks, runner
from .generation import GenerationConfig, GenerationMode, HeaderConfig
from .models import Case

__version__ = SCHEMATHESIS_VERSION

# Public API
auth = auths.GLOBAL_AUTH_STORAGE
hook = hooks.register

__all__ = [
    "Case",
    "CheckContext",
    "CheckFunction",
    "GenerationMode",
    "GenerationConfig",
    "HeaderConfig",
    "OutputConfig",
    "Response",
    "TargetContext",
    "TargetFunction",
    "__version__",
    "auth",
    "check",
    "contrib",
    "errors",
    "experimental",
    "graphql",
    "hook",
    "hooks",
    "openapi",
    "pytest",
    "python",
    "runner",
    "sanitization",
    "target",
]
