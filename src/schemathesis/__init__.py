from __future__ import annotations

from schemathesis import errors, graphql, openapi, pytest
from schemathesis.auths import AuthContext, auth
from schemathesis.checks import CheckContext, CheckFunction, check
from schemathesis.core.transport import Response
from schemathesis.core.version import SCHEMATHESIS_VERSION
from schemathesis.generation import GenerationMode
from schemathesis.generation.case import Case
from schemathesis.generation.metrics import MetricContext, MetricFunction, metric
from schemathesis.hooks import HookContext, hook
from schemathesis.schemas import BaseSchema
from schemathesis.transport import SerializationContext, serializer

__version__ = SCHEMATHESIS_VERSION

__all__ = [
    "Case",
    "GenerationMode",
    "Response",
    "BaseSchema",
    "__version__",
    "errors",
    # Spec or usage specific namespaces
    "openapi",
    "graphql",
    "pytest",
    # Hooks
    "hook",
    "HookContext",
    # Checks
    "check",
    "CheckContext",
    "CheckFunction",
    # Auth
    "auth",
    "AuthContext",
    # Targeted Property-based Testing
    "metric",
    "MetricContext",
    "MetricFunction",
    # Serialization
    "serializer",
    "SerializationContext",
]
