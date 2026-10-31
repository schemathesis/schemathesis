from __future__ import annotations

import json
import os
import socket
import struct
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path
from typing import Any, TypeVar

import hypothesis
import pytest
import requests
import urllib3
import yaml
from hypothesis import HealthCheck, given, settings
from hypothesis.database import InMemoryExampleDatabase

from schemathesis import Case
from schemathesis.checks import CheckContext
from schemathesis.config import ChecksConfig
from schemathesis.core.deserialization import deserialize_yaml
from schemathesis.core.errors import format_exception
from schemathesis.core.transforms import deepclone
from schemathesis.engine import Status, events, from_schema
from schemathesis.engine.events import EngineEvent, EngineFinished, NonFatalError, ScenarioFinished
from schemathesis.engine.recorder import Interaction
from schemathesis.engine.run import Phase, PhaseName
from schemathesis.generation.stateful.state_machine import DEFAULT_STATE_MACHINE_SETTINGS, APIStateMachine
from test.apps import builders

HERE = os.path.dirname(os.path.abspath(__file__))

# Hypothesis renamed this header in 6.159.0 ("Falsifying example" -> "Failing test case"). Tests that
# assert the header is *present* fail loudly on a future rename, which is what keeps the tests asserting
# its absence from silently passing against a string Hypothesis no longer emits.
HYPOTHESIS_FAILURE_HEADERS = ("Failing test case", "Falsifying example")


def profile_max_examples() -> int | None:
    # `max_examples` set via `--hypothesis-profile`, or None under the default profile.
    value = settings().max_examples
    return None if value == 100 else value


def run_given(
    *args: Any,
    max_examples: int,
    derandomize: bool = False,
    phases: Sequence[hypothesis.Phase] | None = None,
    seed: int | None = None,
    **kwargs: Any,
) -> Callable[[Callable], None]:
    """Run the decorated function as a Hypothesis test right away; `--hypothesis-profile` overrides `max_examples`."""
    options: dict[str, Any] = {"derandomize": True} if derandomize else {}
    if phases is not None:
        options["phases"] = phases

    def run(test: Callable) -> None:
        wrapped = given(*args, **kwargs)(
            settings(
                max_examples=profile_max_examples() or max_examples,
                deadline=None,
                database=None,
                suppress_health_check=list(HealthCheck),
                **options,
            )(test)
        )
        if seed is not None:
            wrapped = hypothesis.seed(seed)(wrapped)
        wrapped()

    return run


def objects_at(value: Any, path: Sequence[str]) -> list[Any]:
    """Values at `path` in `value`, where `*` stands for every array item."""
    if not path:
        return [value]
    head, *rest = path
    if head == "*":
        return [found for item in value for found in objects_at(item, rest)] if isinstance(value, list) else []
    return objects_at(value[head], rest) if isinstance(value, dict) and head in value else []


def has_hypothesis_failure_header(text: str, test_name: str = "") -> bool:
    return any(f"{header}: {test_name}" in text for header in HYPOTHESIS_FAILURE_HEADERS)


def get_schema_path(schema_name: str) -> str:
    return os.path.join(HERE, "data", schema_name)


def to_float32(value: float) -> float:
    """Narrow a double to single precision, mirroring how a server stores a `format: float` value."""
    try:
        return struct.unpack("<f", struct.pack("<f", value))[0]
    except OverflowError:
        return float("inf") if value > 0 else float("-inf")


SIMPLE_PATH = get_schema_path("simple_swagger.yaml")


def make_openapi_schema(
    paths: dict[str, Any] | None = None, *, version: str = "3.0.2", **kwargs: Any
) -> dict[str, Any]:
    return builders.build_schema(paths, version=version, **kwargs)


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


def assert_cli_snapshot(result: Any, snapshot: Any) -> None:
    """Match a CLI Result against ``snapshot_cli``; show un-normalised stdout on failure."""
    raw_stdout = getattr(result, "stdout", "") or ""
    assert result == snapshot, f"\n--- raw stdout ---\n{raw_stdout}\n--- end ---"


def load_yaml_or_fail(path: str | os.PathLike, *, context: str = "") -> dict:
    """Load YAML; surface raw file + caller context if it doesn't parse to a dict."""
    raw = Path(path).read_text(encoding="utf-8")
    parsed = yaml.safe_load(raw)
    assert isinstance(parsed, dict), (
        f"YAML at {path} parsed to {type(parsed).__name__} (expected dict). "
        f"File size: {len(raw)} bytes.{_format_context(context)}\n--- raw ---\n{raw}\n--- end raw ---"
    )
    return parsed


def load_json_or_fail(path: str | os.PathLike, *, context: str = "") -> Any:
    """Load JSON; surface raw file + caller context on parse failure or None result."""
    raw = Path(path).read_text(encoding="utf-8")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AssertionError(
            f"JSON at {path} failed to parse: {exc}. File size: {len(raw)} bytes."
            f"{_format_context(context)}\n--- raw ---\n{raw}\n--- end raw ---"
        ) from exc
    assert parsed is not None, (
        f"JSON at {path} parsed to None. File size: {len(raw)} bytes."
        f"{_format_context(context)}\n--- raw ---\n{raw}\n--- end raw ---"
    )
    return parsed


def _format_context(context: str) -> str:
    return f"\n--- context ---\n{context}\n--- end context ---" if context else ""


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
        max_time=None,
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
        database = schema.config.generation.database
        if database is None and not deterministic:
            database = "none"
        schema.config.generation.update(
            max_examples=max_examples,
            deterministic=deterministic,
            with_security_parameters=with_security_parameters,
            modes=modes,
            database=database,
        )
        schema.config.update(headers=headers, workers=workers, request_timeout=request_timeout, tls_verify=tls_verify)
        if auth is not None:
            schema.config.auth.update(basic=auth)
        if seed is not None:
            schema.config.seed = seed
        schema.config.max_failures = max_failures
        schema.config.max_time = max_time
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


def check_context(config=None, *, recorder=None, override=None, headers=None, transport_kwargs=None):
    return CheckContext(
        override=override,
        auth=None,
        headers=headers,
        config=config or ChecksConfig(),
        transport_kwargs=transport_kwargs,
        recorder=recorder,
        response_checks=None,
    )


def make_pytest_outcome_test(testdir: Any, ctx: Any, outcome: str, report_config: str, *, lazy: bool = False) -> str:
    """Write a one-operation pytest test that ends with `outcome` and return its operation label."""
    marker = ""
    config = ""
    if outcome == "check_failure":
        api = ctx.openapi.apps.failure()
        schema = f'schemathesis.openapi.from_url("{api.schema_url}")'
        body = "case.call_and_validate()"
        label = "GET /api/failure"
    elif outcome == "unsatisfiable":
        api = ctx.openapi.apps.unsatisfiable()
        schema = f'schemathesis.openapi.from_url("{api.schema_url}")'
        config = "schema.config.generation.update(modes=[GenerationMode.POSITIVE])"
        body = "case.call_and_validate()"
        label = "POST /api/unsatisfiable"
    elif outcome == "network_error":
        schema_dict = ctx.openapi.build_schema({"/network": {"get": {"responses": {"200": {"description": "OK"}}}}})
        schema = f"schemathesis.openapi.from_dict({schema_dict!r})"
        config = 'schema.config.update(base_url="http://127.0.0.1:1")'
        body = "case.call()"
        label = "GET /network"
    elif outcome == "schema_error":
        schema_dict = ctx.openapi.build_schema(
            {
                "/invalid": {
                    "get": {
                        "parameters": [{"$ref": "#/components/parameters/Missing"}],
                        "responses": {"200": {"description": "OK"}},
                    }
                }
            }
        )
        schema = f"schemathesis.openapi.from_dict({schema_dict!r})"
        body = "case.call()"
        label = "GET /invalid"
    else:
        api = ctx.openapi.apps.success()
        schema = f'schemathesis.openapi.from_url("{api.schema_url}")'
        body = {
            "assertion": 'case.call(); assert False, "boom"',
            "runtime_error": 'case.call(); raise RuntimeError("bug")',
            "skip": 'pytest.skip("why")',
            "mark_skip": "case.call()",
        }[outcome]
        if outcome == "mark_skip":
            marker = '@pytest.mark.skip(reason="why")'
        label = "GET /api/success"
    if lazy:
        setup = "\n    ".join(line for line in (f"schema = {schema}", config, report_config, "return schema") if line)
        source = f"""
@pytest.fixture
def api_schema():
    {setup}

lazy_schema = schemathesis.pytest.from_fixture("api_schema")

{marker}
@lazy_schema.parametrize()
@settings(max_examples=1, phases=[Phase.generate])
def test_api(case):
    {body}
"""
    else:
        source = f"""
schema = {schema}
{config}
{report_config}

{marker}
@schema.parametrize()
@settings(max_examples=1, phases=[Phase.generate])
def test_api(case):
    {body}
"""
    testdir.make_test(source)
    return label


@contextmanager
def refusing_url() -> Iterator[str]:
    # A bound socket that never listens refuses every connection, and no other server can take its port meanwhile.
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        yield f"http://127.0.0.1:{sock.getsockname()[1]}"


@contextmanager
def silent_url() -> Iterator[str]:
    # A listening socket that never accepts completes the handshake, then never answers.
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen()
        yield f"http://127.0.0.1:{sock.getsockname()[1]}"


def allure_results(allure_dir: str | os.PathLike) -> list[Any]:
    result_files = list(Path(allure_dir).glob("*-result.json"))
    assert result_files
    return [load_json_or_fail(path) for path in result_files]


def allure_outcomes(allure_dir: str | os.PathLike) -> dict[str, tuple[str, str]]:
    outcomes = {}
    for result in allure_results(allure_dir):
        raw_message = result.get("statusDetails", {}).get("message", "")
        message = raw_message.splitlines()[0] if raw_message else ""
        if message.startswith("ConnectionError: "):
            message = "ConnectionError"
        outcomes[result["name"]] = (result["status"], message)
    return outcomes


def crash_cache_dir(tmp_path: Path) -> Path:
    return tmp_path / ".schemathesis" / "default" / "cache" / "crashes"


def openapi_url(ctx):
    return ctx.openapi.apps.success().schema_url


def graphql_url(ctx):
    return ctx.graphql.apps.books().schema_url


def stateful_phase() -> Phase:
    return Phase(name=PhaseName.STATEFUL_TESTING, is_enabled=True)


def saved_example_keys(state_machine: type[APIStateMachine]) -> set[bytes]:
    """Example database keys a state machine saves to when its first response fails."""
    database = InMemoryExampleDatabase()

    class Failing(state_machine):
        def validate_response(self, response: Any, case: Case, **kwargs: Any) -> None:
            raise AssertionError

    with pytest.raises(AssertionError):
        Failing.run(settings=settings(DEFAULT_STATE_MACHINE_SETTINGS, database=database, max_examples=1))
    return set(database.data)
