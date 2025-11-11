import hypothesis.errors
import pytest
from hypothesis import HealthCheck, Phase, given, settings
from hypothesis_openapi import openapis

import schemathesis
import schemathesis.engine
from schemathesis.config import HealthCheck as SchemathesisHealthCheck
from schemathesis.config import SchemathesisConfig
from schemathesis.core.errors import InvalidSchema
from schemathesis.engine import events

IGNORED_EXCEPTIONS = (hypothesis.errors.Unsatisfiable, InvalidSchema, hypothesis.errors.FailedHealthCheck)
config = SchemathesisConfig.from_dict({})
config.projects.default.update(suppress_health_check=[SchemathesisHealthCheck.all])
config.projects.default.phases.update(phases=["examples", "fuzzing"])
config.projects.default.generation.update(max_examples=10)


@given(schema=openapis(version="2.0") | openapis(version="3.0"))
@settings(max_examples=20, phases=[Phase.generate], deadline=None, suppress_health_check=list(HealthCheck))
@pytest.mark.usefixtures("mocked_call")
def test_random_schemas(schema):
    schema = schemathesis.openapi.from_dict(schema, config=config)
    for event in schemathesis.engine.from_schema(schema).execute():
        assert not isinstance(event, events.FatalError), repr(event)
        if isinstance(event, events.NonFatalError) and not isinstance(event.value, IGNORED_EXCEPTIONS):
            raise AssertionError(str(event.info)) from event.value
