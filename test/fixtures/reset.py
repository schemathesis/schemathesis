from __future__ import annotations

import pytest

from schemathesis import auths, hooks
from schemathesis.cli.commands import fuzz as fuzz_command
from schemathesis.cli.commands import run as run_command
from schemathesis.cli.ext.groups import GROUPS, GroupedOption
from schemathesis.cli.ext.handlers import CUSTOM_HANDLERS
from schemathesis.core import deserialization
from schemathesis.core.jsonschema import _validator_failure_cache, validator_cache
from schemathesis.core.jsonschema.resolver import _load_remote_uri
from schemathesis.generation.coverage import DEFAULT_GENERATION_SESSION
from schemathesis.generation.hypothesis import canonical_strategy_cache, custom_formats_cache
from schemathesis.specs.openapi import media_types
from schemathesis.specs.openapi.examples import load_external_example
from schemathesis.specs.openapi.formats import STRING_FORMATS
from schemathesis.transport.asgi import ASGI_TRANSPORT
from schemathesis.transport.requests import REQUESTS_TRANSPORT
from schemathesis.transport.wsgi import WSGI_TRANSPORT

TRANSPORTS = (ASGI_TRANSPORT, WSGI_TRANSPORT, REQUESTS_TRANSPORT)


@pytest.fixture(autouse=True)
def reset_hooks():
    # Store built-in deserializers to restore after test
    builtin_deserializers = dict(deserialization._DESERIALIZERS)
    builtin_string_formats = set(STRING_FORMATS.keys())
    builtin_groups = set(GROUPS.keys())
    # Registering a media type overwrites a transport's serializer for it, built-in ones included.
    builtin_serializers = [(transport, dict(transport._serializers)) for transport in TRANSPORTS]

    CUSTOM_HANDLERS.clear()
    hooks.unregister_all()
    auths.unregister()
    media_types.unregister_all()
    yield
    CUSTOM_HANDLERS.clear()
    hooks.unregister_all()
    auths.unregister()
    media_types.unregister_all()
    for transport, serializers in builtin_serializers:
        transport._serializers.clear()
        transport._serializers.update(serializers)
    # Restore built-in deserializers
    deserialization._DESERIALIZERS.clear()
    deserialization._DESERIALIZERS.update(builtin_deserializers)
    # Remove any string formats registered during the test
    for name in list(STRING_FORMATS.keys()):
        if name not in builtin_string_formats:
            del STRING_FORMATS[name]
    # Remove any CLI option groups registered during the test
    for name in list(GROUPS.keys()):
        if name not in builtin_groups:
            del GROUPS[name]
    for command in (run_command, fuzz_command):
        command.params[:] = [
            p for p in command.params if not (isinstance(p, GroupedOption) and p.group not in builtin_groups)
        ]
    # Process-wide caches; clear so monkeypatched strategies in one test don't leak cached results.
    custom_formats_cache.clear()
    # Formats are dropped from the registry above without going through `unregister_string_format`.
    canonical_strategy_cache.clear()
    validator_cache.clear()
    _validator_failure_cache.clear()
    DEFAULT_GENERATION_SESSION.close()
    # Test servers stop with their test, so a later test may get the same port and must not see a cached response.
    _load_remote_uri.cache_clear()
    load_external_example.cache_clear()
