from __future__ import annotations

import os
from collections.abc import Callable
from functools import lru_cache, wraps
from typing import Any, TypeVar

import pytest
import requests
import urllib3
from syrupy import SnapshotAssertion

import schemathesis
from schemathesis import Case
from schemathesis.core.deserialization import deserialize_yaml
from schemathesis.core.errors import format_exception
from schemathesis.engine import Status, events, from_schema
from schemathesis.engine.events import EngineEvent, EngineFinished, NonFatalError, ScenarioFinished
from schemathesis.engine.phases import PhaseName
from schemathesis.engine.recorder import Interaction
from schemathesis.schemas import BaseSchema

HERE = os.path.dirname(os.path.abspath(__file__))


def get_schema_path(schema_name: str) -> str:
    return os.path.join(HERE, "data", schema_name)


SIMPLE_PATH = get_schema_path("simple_swagger.yaml")


def get_schema(schema_name: str = "simple_swagger.yaml", **kwargs: Any) -> BaseSchema:
    schema = make_schema(schema_name, **kwargs)
    return schemathesis.openapi.from_dict(schema)


def merge_recursively(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    """Merge two dictionaries recursively."""
    for key in b:
        if key in a:
            if isinstance(a[key], dict) and isinstance(b[key], dict):
                merge_recursively(a[key], b[key])
            else:
                a[key] = b[key]
        else:
            a[key] = b[key]
    return a


def make_schema(schema_name: str = "simple_swagger.yaml", **kwargs: Any) -> dict[str, Any]:
    schema = load_schema(schema_name)
    return merge_recursively(kwargs, schema)


@lru_cache
def load_schema(schema_name: str) -> dict[str, Any]:
    path = get_schema_path(schema_name)
    with open(path) as fd:
        return deserialize_yaml(fd)


def integer(**kwargs: Any) -> dict[str, Any]:
    return {"type": "integer", "in": "query", **kwargs}


def as_param(*parameters: Any) -> dict[str, Any]:
    return {"paths": {"/users": {"get": {"parameters": list(parameters), "responses": {"200": {"description": "OK"}}}}}}


def noop(value: Any) -> bool:
    return True


def _assert_value(value: Any, type: type, predicate: Callable = noop) -> None:
    assert isinstance(value, type)
    assert predicate(value)


def assert_int(value: Any, predicate: Callable = noop) -> None:
    _assert_value(value, int, predicate)


def assert_str(value: Any, predicate: Callable = noop) -> None:
    _assert_value(value, str, predicate)


def assert_list(value: Any, predicate: Callable = noop) -> None:
    _assert_value(value, list, predicate)


def assert_requests_call(case: Case):
    """Verify that all generated input parameters are usable by requests."""
    with pytest.raises((requests.exceptions.ConnectionError, urllib3.exceptions.NewConnectionError)):
        # On Windows it may take time to get the connection error, hence we set a timeout
        case.call(base_url="http://127.0.0.1:1", timeout=0.001)


def flaky(*, max_runs: int, min_passes: int):
    """A decorator to mark a test as flaky."""

    def decorate(test):
        @wraps(test)
        def inner(*args, **kwargs):
            snapshot_fixture_name = None
            snapshot_cli = None
            for name, kwarg in kwargs.items():
                if isinstance(kwarg, SnapshotAssertion):
                    snapshot_fixture_name = name
                    snapshot_cli = kwarg
                    break
            runs = passes = 0
            while passes < min_passes:
                runs += 1
                try:
                    test(*args, **kwargs)
                except Exception:
                    if snapshot_fixture_name is not None:
                        kwargs[snapshot_fixture_name] = snapshot_cli.rebuild()
                    if runs >= max_runs:
                        raise
                else:
                    passes += 1

        return inner

    return decorate


E = TypeVar("E", bound=EngineEvent)


class EventStream:
    def __init__(
        self,
        schema,
        *,
        checks=None,
        phases=None,
        seed=None,
        max_examples=None,
        deterministic=None,
        headers=None,
        auth=None,
        workers=1,
        max_failures=None,
        request_timeout=None,
        tls_verify=None,
        with_security_parameters=True,
        parameters=None,
        max_steps=None,
        modes=None,
    ):
        schema.config.checks.update(
            included_check_names=[c.__name__ for c in checks] if checks else ["not_a_server_error"],
        )
        phases = phases or [PhaseName.EXAMPLES, PhaseName.FUZZING, PhaseName.STATEFUL_TESTING]
        schema.config.phases.update(phases=[phase.value.lower() for phase in phases])
        schema.config.generation.update(
            max_examples=max_examples,
            deterministic=deterministic,
            with_security_parameters=with_security_parameters,
            modes=modes,
        )
        schema.config.update(headers=headers, workers=workers, request_timeout=request_timeout, tls_verify=tls_verify)
        if auth is not None:
            schema.config.auth.update(basic=auth)
        schema.config.seed = seed
        schema.config.max_failures = max_failures
        if max_steps is not None:
            schema.config.phases.stateful.max_steps = max_steps
        if parameters is not None:
            result = schema.config.parameters or {}
            for name, value in parameters.items():
                result[name] = value
            schema.config.parameters = result
        self.schema = from_schema(schema)

    def execute(self) -> EventStream:
        self.events = list(self.schema.execute())
        return self

    def find(self, ty: type[E], **attrs) -> E | None:
        """Find first event of specified type matching all attribute predicates."""
        return next(
            (
                e
                for e in self.events
                if isinstance(e, ty)
                and all(v(getattr(e, k)) if callable(v) else getattr(e, k) == v for k, v in attrs.items())
            ),
            None,
        )

    def find_all(self, ty: type[E], **attrs) -> list[E]:
        """Find all events of specified type matching all attribute predicates."""
        return [
            e
            for e in self.events
            if isinstance(e, ty)
            and all(v(getattr(e, k)) if callable(v) else getattr(e, k) == v for k, v in attrs.items())
        ]

    def find_all_interactions(self) -> list[Interaction]:
        return sum([list(event.recorder.interactions.values()) for event in self.find_all(events.ScenarioFinished)], [])

    def assert_errors(self):
        assert self.find(NonFatalError) is not None

    def assert_no_errors(self):
        event = self.find(NonFatalError)
        assert event is None, format_exception(event.value)

    def assert_after_execution_status(self, status: Status) -> None:
        assert self.find_all(ScenarioFinished)[-1].status == status

    @property
    def failures_count(self) -> int:
        result = 0
        for event in self.events:
            if (isinstance(event, events.ScenarioFinished) and event.status == Status.FAILURE) or (
                isinstance(event, events.PhaseFinished)
                and event.phase.name == PhaseName.STATEFUL_TESTING
                and event.phase.is_enabled
                and event.status == Status.FAILURE
            ):
                result += 1
        return result

    def assert_no_failures(self):
        assert self.failures_count == 0

    @property
    def finished(self) -> EngineFinished | None:
        return self.find(EngineFinished)
