from __future__ import annotations

import pytest

from schemathesis import auths, hooks
from schemathesis.cli.commands import fuzz as fuzz_command
from schemathesis.cli.commands import run as run_command
from schemathesis.cli.ext.groups import GROUPS, GroupedOption
from schemathesis.cli.ext.handlers import CUSTOM_HANDLERS
from schemathesis.core import deserialization
from schemathesis.core.jsonschema import _validator_failure_cache, validator_cache
from schemathesis.core.media_types import MEDIA_TYPE_STRATEGIES
from schemathesis.generation.hypothesis import custom_formats_cache, schema_generation_cache
from schemathesis.specs.openapi import media_types
from schemathesis.specs.openapi.coverage._schema import _REMOVE_EXAMPLES_CACHE
from schemathesis.specs.openapi.formats import STRING_FORMATS
from schemathesis.transport.asgi import ASGI_TRANSPORT
from schemathesis.transport.requests import REQUESTS_TRANSPORT
from schemathesis.transport.wsgi import WSGI_TRANSPORT


@pytest.fixture(autouse=True)
def reset_hooks():
    # Store built-in deserializers to restore after test
    builtin_deserializers = deserialization.deserializers().copy()
    builtin_string_formats = set(STRING_FORMATS.keys())
    builtin_groups = set(GROUPS.keys())

    CUSTOM_HANDLERS.clear()
    hooks.unregister_all()
    auths.unregister()
    for transport in (ASGI_TRANSPORT, WSGI_TRANSPORT, REQUESTS_TRANSPORT):
        transport.unregister_serializer(*MEDIA_TYPE_STRATEGIES.keys())
    media_types.unregister_all()
    yield
    CUSTOM_HANDLERS.clear()
    hooks.unregister_all()
    auths.unregister()
    for transport in (ASGI_TRANSPORT, WSGI_TRANSPORT, REQUESTS_TRANSPORT):
        transport.unregister_serializer(*MEDIA_TYPE_STRATEGIES.keys())
    media_types.unregister_all()
    # Restore built-in deserializers
    current = list(deserialization.deserializers().keys())
    deserialization.unregister_deserializer(*current)
    for media_type, func in builtin_deserializers.items():
        deserialization.register_deserializer(func, media_type)
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
    schema_generation_cache.clear()
    custom_formats_cache.clear()
    validator_cache.clear()
    _validator_failure_cache.clear()
    _REMOVE_EXAMPLES_CACHE.clear()
