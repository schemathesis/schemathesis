from __future__ import annotations

from schemathesis import auths, engine, errors, graphql, hooks, openapi, pytest, python
from schemathesis.checks import CheckContext, CheckFunction, check
from schemathesis.core.transport import Response
from schemathesis.core.version import SCHEMATHESIS_VERSION
from schemathesis.generation import GenerationMode
from schemathesis.generation.case import Case
from schemathesis.generation.targets import TargetContext, TargetFunction, target
from schemathesis.hooks import HookContext
from schemathesis.schemas import BaseSchema
from schemathesis.transport import SerializationContext, serializer

__version__ = SCHEMATHESIS_VERSION

# Public API
auth = auths.GLOBAL_AUTH_STORAGE
hook = hooks.register

__all__ = [
    "Case",
    "CheckContext",
    "CheckFunction",
    "GenerationMode",
    "Response",
    "TargetContext",
    "TargetFunction",
    "HookContext",
    "BaseSchema",
    "__version__",
    "auth",
    "check",
    "engine",
    "errors",
    "graphql",
    "hook",
    "hooks",
    "openapi",
    "pytest",
    "python",
    "target",
    # Serialization
    "serializer",
    "SerializationContext",
]
