from __future__ import annotations

import threading

import pytest

import schemathesis
from schemathesis.config import SchemathesisConfig
from schemathesis.engine.context import EngineContext
from schemathesis.engine.run import Phase, PhaseName, stateful
from schemathesis.generation.modes import GenerationMode
from schemathesis.specs.openapi.extra_data_source import OpenApiExtraDataSource


def _stateful_phase() -> Phase:
    return Phase(name=PhaseName.STATEFUL_TESTING, is_supported=True, is_enabled=True)


def _run_stateful(ctx, stop_event, *, max_examples=10):
    api = ctx.openapi.apps.stateful_users()
    config = SchemathesisConfig()
    config.projects.override.phases.stateful.inference.algorithms = []
    config.projects.override.generation.update(
        modes=[GenerationMode.POSITIVE],
        unique_inputs=False,
        max_examples=max_examples,
        database="none",
    )
    schema = schemathesis.openapi.from_url(api.schema_url, config=config)
    engine_ctx = EngineContext(schema=schema, stop_event=stop_event)
    list(stateful.execute(engine=engine_ctx, phase=_stateful_phase()))
    return engine_ctx


@pytest.fixture
def stateful_run(ctx):
    return _run_stateful(ctx, threading.Event())


def test_stateful_harvest_flows_into_pool(stateful_run):
    extra_data_source = stateful_run.extra_data_source
    assert isinstance(extra_data_source, OpenApiExtraDataSource)
    assert list(extra_data_source.repository.iter_instances("User")), (
        "stateful 2xx bodies were not ingested into the pool at the suite boundary"
    )
