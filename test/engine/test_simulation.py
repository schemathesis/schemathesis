import random
import threading

import pytest
import requests
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from requests.exceptions import HTTPError, Timeout

from schemathesis.config import SchemathesisConfig
from schemathesis.engine.context import EngineContext
from schemathesis.engine.core import Engine
from schemathesis.engine.events import FatalError


class FaultInjectingSession(requests.Session):
    def __init__(self, random):
        super().__init__()
        self.random = random

    def request(self, method, url, **kwargs):
        choice = self.random.random()

        if choice < 0.05:
            raise Timeout("Injected timeout")
        elif choice < 0.10:
            raise ConnectionError("Injected connection error")
        elif choice < 0.15:
            raise HTTPError("Injected HTTP error")
        return super().request(method, url, **kwargs)


@given(
    seed=st.integers(),
    config=st.from_type(SchemathesisConfig),
)
@pytest.mark.operations("__all__")
@settings(max_examples=6, suppress_health_check=list(HealthCheck), deadline=None)
def test_engine_with_faults(seed, config, openapi3_schema):
    session = FaultInjectingSession(random=random.Random(seed))
    openapi3_schema.config = config.projects.default
    ctx = EngineContext(schema=openapi3_schema, stop_event=threading.Event(), session=session)
    engine = Engine(schema=openapi3_schema)
    plan = engine._create_execution_plan()
    for event in plan.execute(ctx):
        assert not isinstance(event, FatalError)
