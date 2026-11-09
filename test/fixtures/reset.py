from __future__ import annotations

import pytest

from schemathesis import auths, hooks
from schemathesis.checks import CHECKS
from schemathesis.cli.commands import fuzz as fuzz_command
from schemathesis.cli.commands import run as run_command
from schemathesis.cli.ext.groups import GROUPS, GroupedOption
from schemathesis.cli.ext.handlers import CUSTOM_HANDLERS
from schemathesis.config import SchemathesisConfig
from schemathesis.core import deserialization
from schemathesis.core.error_feedback.pipeline import _reset_pipeline_for_tests
from schemathesis.core.jsonschema.resolver import _REMOTE_FAILURES, _load_remote_uri, load_file, load_file_uri
from schemathesis.generation.metrics import METRICS
from schemathesis.python._constants.registry import default_registry
from schemathesis.specs.graphql.scalars import CUSTOM_SCALARS
from schemathesis.specs.openapi import media_types
from schemathesis.specs.openapi._caches import clear_all
from schemathesis.specs.openapi.examples import load_external_example
from schemathesis.specs.openapi.formats import STRING_FORMATS
from schemathesis.transport.asgi import ASGI_TRANSPORT
from schemathesis.transport.requests import REQUESTS_TRANSPORT
from schemathesis.transport.wsgi import WSGI_TRANSPORT

TRANSPORTS = (ASGI_TRANSPORT, WSGI_TRANSPORT, REQUESTS_TRANSPORT)
# A test may register new entries or override built-in ones; each is restored to its built-in entries.
REGISTRIES = (CHECKS, METRICS)

# Global state a test may fill in, emptied after every test.
CLEARS = (
    CUSTOM_HANDLERS.clear,
    hooks.unregister_all,
    auths.unregister,
    media_types.unregister_all,
    CUSTOM_SCALARS.clear,
    default_registry().clear,
    # Remembers the last matching parser, so a previous test would pick the parser for the next one.
    _reset_pipeline_for_tests,
    # Drops monkeypatched strategies and formats removed from the registry without `unregister_string_format`.
    clear_all,
    # Test servers and files go away with their test, so a later test must not see a cached document or failure.
    _load_remote_uri.cache_clear,
    _REMOTE_FAILURES.clear,
    load_external_example.cache_clear,
    load_file.cache_clear,
    load_file_uri.cache_clear,
)


@pytest.fixture(autouse=True)
def reset_global_state():
    builtin_entries = [(registry, registry.get_all()) for registry in REGISTRIES]
    builtin_deserializers = dict(deserialization._DESERIALIZERS)
    builtin_string_formats = set(STRING_FORMATS)
    builtin_groups = set(GROUPS)
    # Registering a media type overwrites a transport's serializer for it, built-in ones included.
    builtin_serializers = [(transport, dict(transport._serializers)) for transport in TRANSPORTS]
    # Under pytest-xdist every worker shares one seed; each test (and each rerun) needs its own.
    SchemathesisConfig.shared_seed = None
    yield
    for registry, entries in builtin_entries:
        builtin_names = {entry.__name__ for entry in entries}
        for name in registry.get_all_names():
            if name not in builtin_names:
                registry.unregister(name)
        for entry in entries:
            registry.register(entry)
    deserialization._DESERIALIZERS.clear()
    deserialization._DESERIALIZERS.update(builtin_deserializers)
    for transport, serializers in builtin_serializers:
        transport._serializers.clear()
        transport._serializers.update(serializers)
    for name in set(STRING_FORMATS) - builtin_string_formats:
        del STRING_FORMATS[name]
    for name in set(GROUPS) - builtin_groups:
        del GROUPS[name]
    for command in (run_command, fuzz_command):
        command.params[:] = [
            param
            for param in command.params
            if not (isinstance(param, GroupedOption) and param.group not in builtin_groups)
        ]
    for clear in CLEARS:
        clear()
