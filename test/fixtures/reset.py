from __future__ import annotations

import pytest

from schemathesis import auths, hooks
from schemathesis.cli.commands import fuzz as fuzz_command
from schemathesis.cli.commands import run as run_command
from schemathesis.cli.ext.groups import GROUPS, GroupedOption
from schemathesis.cli.ext.handlers import CUSTOM_HANDLERS
from schemathesis.core import deserialization
from schemathesis.core.jsonschema.resolver import _REMOTE_FAILURES, _load_remote_uri
from schemathesis.specs.openapi import media_types
from schemathesis.specs.openapi._caches import clear_all
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
    # Formats are dropped from the registry above without going through `unregister_string_format`, and
    # monkeypatched strategies in one test must not leak cached results into the next.
    clear_all()
    # Test servers stop with their test, so a later test on the same port must not see a cached document or failure.
    _load_remote_uri.cache_clear()
    _REMOTE_FAILURES.clear()
    load_external_example.cache_clear()
