from __future__ import annotations

from schemathesis import auths, engine, errors, graphql, openapi, pytest
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

# Public API
auth = auths.GLOBAL_AUTH_STORAGE

__all__ = [
    "Case",
    "CheckContext",
    "CheckFunction",
    "GenerationMode",
    "Response",
    "BaseSchema",
    "__version__",
    "auth",
    "check",
    "engine",
    "errors",
    "graphql",
    "openapi",
    # Pytest loader
    "pytest",
    # Hooks
    "hook",
    "HookContext",
    # Targeted Property-based Testing
    "metric",
    "MetricContext",
    "MetricFunction",
    # Serialization
    "serializer",
    "SerializationContext",
]
