from __future__ import annotations

from schemathesis import errors, graphql, grpc, openapi, pytest, python, stateful
from schemathesis.auths import AuthContext, AuthProvider, auth
from schemathesis.checks import CheckContext, CheckFunction, CheckResult, check
from schemathesis.config import SchemathesisConfig as Config
from schemathesis.core.deserialization import DeserializationContext, deserializer
from schemathesis.core.transport import Response
from schemathesis.core.version import SCHEMATHESIS_VERSION
from schemathesis.generation import GenerationMode
from schemathesis.generation.case import Case
from schemathesis.generation.metrics import MetricContext, MetricFunction, metric
from schemathesis.hooks import HookContext, extension, hook
from schemathesis.registrations import scoped_extensions
from schemathesis.schemas import APIOperation, BaseSchema
from schemathesis.transport import SerializationContext, serializer

__version__ = SCHEMATHESIS_VERSION

__all__ = [
    "__version__",
    "Case",
    "Response",
    "APIOperation",
    "BaseSchema",
    "Config",
    "GenerationMode",
    "stateful",
    "errors",
    "openapi",
    "graphql",
    "grpc",
    "pytest",
    "python",
    "hook",
    "HookContext",
    "extension",
    "scoped_extensions",
    "check",
    "CheckContext",
    "CheckFunction",
    "CheckResult",
    "auth",
    "AuthContext",
    "AuthProvider",
    "metric",
    "MetricContext",
    "MetricFunction",
    "deserializer",
    "DeserializationContext",
    "serializer",
    "SerializationContext",
]
