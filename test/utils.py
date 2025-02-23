from __future__ import annotations

import os
from functools import lru_cache, wraps
from typing import Any, Callable, TypeVar

import hypothesis
import pytest
import requests
import urllib3
from syrupy import SnapshotAssertion

import schemathesis
from schemathesis import Case
from schemathesis.checks import not_a_server_error
from schemathesis.core.deserialization import deserialize_yaml
from schemathesis.core.errors import format_exception
from schemathesis.core.transforms import deepclone
from schemathesis.engine import Status, events, from_schema
from schemathesis.engine.config import EngineConfig, ExecutionConfig, NetworkConfig
from schemathesis.engine.events import EngineEvent, EngineFinished, NonFatalError, ScenarioFinished
from schemathesis.engine.phases import PhaseName
from schemathesis.engine.recorder import Interaction
from schemathesis.generation.hypothesis import DEFAULT_DEADLINE
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
    schema = deepclone(load_schema(schema_name))
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
    def __init__(self, schema, **options):
        options.setdefault("checks", [not_a_server_error])
        config = EngineConfig(
            execution=ExecutionConfig(
                phases=options.get(
                    "phases", [PhaseName.PROBING, PhaseName.EXAMPLES, PhaseName.FUZZING, PhaseName.STATEFUL_TESTING]
                ),
                checks=options.get("checks", []),
                targets=options.get("targets", []),
                hypothesis_settings=options.get("hypothesis_settings")
                or hypothesis.settings(deadline=DEFAULT_DEADLINE),
                generation=schema.generation_config,
                max_failures=options.get("max_failures"),
                continue_on_failure=options.get("continue_on_failure", False),
                unique_inputs=options.get("unique_data", False),
                seed=options.get("seed"),
                workers_num=options.get("workers_num", 1),
            ),
            network=options.get("network") or NetworkConfig(),
            override=options.get("override"),
            checks_config=options.get("checks_config", {}),
        )

        self.schema = from_schema(schema, config=config)

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
