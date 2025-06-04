from __future__ import annotations

from schemathesis import errors, graphql, openapi, pytest
from schemathesis.auths import AuthContext, auth
from schemathesis.checks import CheckContext, CheckFunction, check
from schemathesis.config import SchemathesisConfig as Config
from schemathesis.core.transport import Response
from schemathesis.core.version import SCHEMATHESIS_VERSION
from schemathesis.generation import GenerationMode
from schemathesis.generation.case import Case
from schemathesis.generation.metrics import MetricContext, MetricFunction, metric
from schemathesis.hooks import HookContext, hook
from schemathesis.schemas import APIOperation, BaseSchema
from schemathesis.transport import SerializationContext, serializer

__version__ = SCHEMATHESIS_VERSION

__all__ = [
    "__version__",
    # Core data structures
    "Case",
    "Response",
    "APIOperation",
    "BaseSchema",
    "Config",
    "GenerationMode",
    # Public errors
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
