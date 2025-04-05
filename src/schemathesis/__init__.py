from __future__ import annotations

from schemathesis import auths, contrib, engine, errors, experimental, graphql, hooks, openapi, pytest, python
from schemathesis.checks import CheckContext, CheckFunction, check
from schemathesis.core.output import OutputConfig, sanitization
from schemathesis.core.transport import Response
from schemathesis.core.version import SCHEMATHESIS_VERSION
from schemathesis.generation import GenerationConfig, GenerationMode, HeaderConfig
from schemathesis.generation.case import Case
from schemathesis.generation.targets import TargetContext, TargetFunction, target
from schemathesis.hooks import HookContext
from schemathesis.schemas import BaseSchema

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
    "HookContext",
    "BaseSchema",
    "__version__",
    "auth",
    "check",
    "contrib",
    "engine",
    "errors",
    "experimental",
    "graphql",
    "hook",
    "hooks",
    "openapi",
    "pytest",
    "python",
    "sanitization",
    "target",
]
