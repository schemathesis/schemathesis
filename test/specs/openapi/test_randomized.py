import sys

import hypothesis.errors
import pytest
from hypothesis import HealthCheck, Phase, given, settings

import schemathesis
import schemathesis.engine
from schemathesis.core.errors import InvalidSchema
from schemathesis.engine import events
from schemathesis.engine.config import EngineConfig, ExecutionConfig

if sys.version_info < (3, 10):
    pytest.skip("Required Python 3.10+", allow_module_level=True)

from hypothesis_openapi import openapis

IGNORED_EXCEPTIONS = (hypothesis.errors.Unsatisfiable, InvalidSchema)


@given(schema=openapis(version="2.0") | openapis(version="3.0"))
@settings(max_examples=20, phases=[Phase.generate], deadline=None, suppress_health_check=list(HealthCheck))
def test_random_schemas(schema, openapi3_base_url):
    schema = schemathesis.openapi.from_dict(schema).configure(base_url=openapi3_base_url)
    for event in schemathesis.engine.from_schema(
        schema,
        config=EngineConfig(
            execution=ExecutionConfig(
                hypothesis_settings=settings(max_examples=10, phases=[Phase.explicit, Phase.generate])
            )
        ),
    ).execute():
        assert not isinstance(event, events.FatalError), repr(event)
        if isinstance(event, events.NonFatalError) and not isinstance(event.value, IGNORED_EXCEPTIONS):
            raise AssertionError(str(event.info)) from event.value
