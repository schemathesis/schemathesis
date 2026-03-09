from __future__ import annotations

import threading

import hypothesis
import pytest

import schemathesis
from schemathesis.config import SchemathesisConfig
from schemathesis.engine.context import EngineContext
from schemathesis.engine.link_calibration import LinkCalibrationState
from schemathesis.engine.run import Phase, PhaseName, stateful
from schemathesis.generation.modes import GenerationMode
from test.apps.catalog.openapi.stateful import UserStore
from test.apps.runtime import Modifier


class CalibrationObserver(LinkCalibrationState):
    """LinkCalibrationState plus a request counter populated by the engine factory."""

    __slots__ = ("target_request_count",)

    def __init__(self) -> None:
        super().__init__()
        self.target_request_count: int = 0


@pytest.fixture
def stop_event():
    return threading.Event()


def _build_stateful_engine_ctx(
    ctx,
    stop_event,
    *modifiers: Modifier[UserStore],
    max_examples=None,
    max_steps=None,
    max_failures=None,
    maximize=None,
    generation_modes=None,
    unique_inputs=False,
    checks=None,
    max_response_time=None,
    headers=None,
    link_calibration: LinkCalibrationState | None = None,
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
    engine_ctx = EngineContext(schema=schema, stop_event=stop_event)
    if link_calibration is not None:
        engine_ctx.link_calibration = link_calibration
    return api, schema, engine_ctx


def _stateful_phase() -> Phase:
    return Phase(name=PhaseName.STATEFUL_TESTING, is_supported=True, is_enabled=True)


@pytest.fixture
def engine_factory(ctx, app_runner, stop_event):
    def _engine_factory(
        *modifiers: Modifier[UserStore],
        hypothesis_settings=None,
        include=None,
        **kwargs,
    ):
        _, schema, engine_ctx = _build_stateful_engine_ctx(ctx, stop_event, *modifiers, **kwargs)
        if hypothesis_settings is not None:
            current = schema.config.get_hypothesis_settings()
            new = hypothesis.settings(current, **hypothesis_settings)
            schema.config.get_hypothesis_settings = lambda *_, **__: new
        if include is not None:
            schema = schema.include(**include)
        return stateful.execute(engine=engine_ctx, phase=_stateful_phase())

    return _engine_factory


@pytest.fixture
def calibration_engine_factory(ctx, stop_event):
    """Engine factory that injects a CalibrationObserver so tests can inspect observation accumulation."""

    def _factory(*modifiers: Modifier[UserStore], max_examples: int = 15) -> CalibrationObserver:
        observer = CalibrationObserver()
        api, _, engine_ctx = _build_stateful_engine_ctx(
            ctx, stop_event, *modifiers, max_examples=max_examples, link_calibration=observer
        )
        list(stateful.execute(engine=engine_ctx, phase=_stateful_phase()))
        observer.target_request_count = api.wsgi_app.config["target_request_count"]["count"]
        return observer

    return _factory
