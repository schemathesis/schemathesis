from __future__ import annotations

import threading

import hypothesis
import pytest

import schemathesis
from schemathesis.config import SchemathesisConfig
from schemathesis.engine.context import EngineContext
from schemathesis.engine.run import Phase, PhaseName, stateful
from schemathesis.generation.modes import GenerationMode
from test.apps.catalog.openapi.stateful import UserStore
from test.apps.runtime import Modifier


@pytest.fixture
def stop_event():
    return threading.Event()


@pytest.fixture
def engine_factory(ctx, app_runner, stop_event):
    def _engine_factory(
        *modifiers: Modifier[UserStore],
        hypothesis_settings=None,
        max_examples=None,
        max_steps=None,
        maximize=None,
        checks=None,
        max_failures=None,
        unique_inputs=False,
        generation_modes=None,
        include=None,
        headers=None,
        max_response_time=None,
    ):
        api = ctx.openapi.apps.stateful_users(*modifiers)
        config = SchemathesisConfig()
        config.update(max_failures=max_failures)
        config.projects.override.checks.update(
            included_check_names=[func.__name__ for func in checks] if isinstance(checks, list) else None,
            max_response_time=max_response_time,
        )
        if max_steps is not None:
            config.projects.override.phases.stateful.max_steps = max_steps
        config.projects.override.phases.stateful.inference.algorithms = []
        config.projects.override.generation.update(
            modes=generation_modes or [GenerationMode.POSITIVE],
            unique_inputs=unique_inputs,
            max_examples=max_examples,
            maximize=maximize,
            database="none",
        )
        config.projects.override.update(headers=headers)
        schema = schemathesis.openapi.from_url(api.schema_url, config=config)

        if hypothesis_settings is not None:
            current = schema.config.get_hypothesis_settings()
            new = hypothesis.settings(current, **hypothesis_settings)
            schema.config.get_hypothesis_settings = lambda *_, **__: new

        if include is not None:
            schema = schema.include(**include)
        return stateful.execute(
            engine=EngineContext(schema=schema, stop_event=stop_event),
            phase=Phase(name=PhaseName.STATEFUL_TESTING, is_supported=True, is_enabled=True),
        )

    return _engine_factory
