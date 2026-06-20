from __future__ import annotations

import inspect
import threading
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, NamedTuple, Protocol, cast

from typing_extensions import TypeIs

from schemathesis.config import ChecksConfig, ConfigError
from schemathesis.core.errors import IncorrectUsage
from schemathesis.core.failures import (
    CustomFailure,
    Failure,
    FailureGroup,
    ResponseTimeExceeded,
    ServerError,
)
from schemathesis.core.registries import Registry
from schemathesis.core.transport import Response
from schemathesis.engine import Status
from schemathesis.generation.overrides import Override

if TYPE_CHECKING:
    from requests.models import CaseInsensitiveDict

    from schemathesis.config import ProjectConfig
    from schemathesis.engine.recorder import ScenarioRecorder
    from schemathesis.engine.run import PhaseName
    from schemathesis.generation.case import Case
    from schemathesis.schemas import BaseSchema

CheckFunction = Callable[["CheckContext", "Response", "Case"], bool | None]


@dataclass(slots=True)
class CheckResult:
    """Outcome of a single validation check."""

    name: str
    status: Status
    failure: Failure | None


class ResponseCheck(Protocol):
    """A class-based check that validates each response."""

    def after_response(self, ctx: CheckContext, response: Response, case: Case) -> bool | None: ...  # pragma: no cover


class RunCheck(Protocol):
    """A class-based check that validates the whole run, once all phases complete."""

    def after_run(self, ctx: CheckContext) -> None: ...  # pragma: no cover


# A registered check class implements `after_response`, `after_run`, or both.
CheckClass = type[ResponseCheck] | type[RunCheck]
# An instantiated check class — the runtime object held by RunChecks.
CheckInstance = ResponseCheck | RunCheck


class ResponseCheckEntry(NamedTuple):
    name: str
    instance: ResponseCheck
    lock: threading.Lock


ResponseChecks = list[ResponseCheckEntry]


class CheckContext:
    """Runtime context passed to validation check functions during API testing.

    Provides access to configuration for currently checked endpoint.
    """

    _override: Override | None
    _auth: tuple[str, str] | None
    _headers: CaseInsensitiveDict | None
    config: ChecksConfig
    """Configuration settings for validation checks."""
    _transport_kwargs: dict[str, Any] | None
    _recorder: ScenarioRecorder | None
    _checks: list[CheckFunction]
    phase: PhaseName | None
    """The testing phase this context belongs to, or `None` for standalone validation."""

    __slots__ = ("_override", "_auth", "_headers", "config", "_transport_kwargs", "_recorder", "_checks", "phase")

    def __init__(
        self,
        override: Override | None,
        auth: tuple[str, str] | None,
        headers: CaseInsensitiveDict | None,
        config: ChecksConfig,
        transport_kwargs: dict[str, Any] | None,
        recorder: ScenarioRecorder | None = None,
        *,
        response_checks: ResponseChecks | None,
        phase: PhaseName | None = None,
    ) -> None:
        self._override = override
        self._auth = auth
        self._headers = headers
        self.config = config
        self._transport_kwargs = transport_kwargs
        self._recorder = recorder
        self.phase = phase
        self._checks = []
        for check in CHECKS.get_all():
            if is_check_class(check):
                continue
            name = check.__name__
            if self.config.get_by_name(name=name).enabled:
                self._checks.append(check)
        if self.config.max_response_time.enabled:
            self._checks.append(max_response_time)
        if response_checks is not None:
            self._checks.extend(build_response_checks(response_checks))

    def _find_parent(self, *, case_id: str) -> Case | None:
        if self._recorder is not None:
            return self._recorder.find_parent(case_id=case_id)
        return None

    def _find_related(self, *, case_id: str) -> Iterator[Case]:
        if self._recorder is not None:
            yield from self._recorder.find_related(case_id=case_id)

    def _find_all_cases(self) -> Iterator[Case]:
        if self._recorder is not None:
            yield from self._recorder.find_all_cases()

    def _find_response(self, *, case_id: str) -> Response | None:
        if self._recorder is not None:
            return self._recorder.find_response(case_id=case_id)
        return None

    def _record_case(self, *, parent_id: str, case: Case) -> None:
        if self._recorder is not None:
            self._recorder.record_case(parent_id=parent_id, case=case, transition=None, is_transition_applied=False)

    def _record_response(self, *, case_id: str, response: Response) -> None:
        if self._recorder is not None:
            self._recorder.record_response(case_id=case_id, response=response)


CheckMethodName = Literal["after_response", "after_run"]


def is_check_class(obj: object) -> TypeIs[CheckClass]:
    return isinstance(obj, type)


def implements_method(obj: object, name: CheckMethodName) -> bool:
    """Whether a check class (or instance) defines the given check method.

    Check classes are a dynamic, user-defined plugin surface, so attribute probing is intentional.
    """
    return callable(getattr(obj, name, None))


# Recognized class-based check methods, each mapped to the Protocol that defines its signature.
CHECK_METHODS: dict[str, type] = {
    "after_response": ResponseCheck,
    "after_run": RunCheck,
}


def _method_parameters(name: CheckMethodName) -> list[str]:
    """Parameter names (excluding `self`) the given check method must accept, per its Protocol."""
    protocol = CHECK_METHODS[name]
    return list(inspect.signature(getattr(protocol, name)).parameters)[1:]


def _available_methods() -> str:
    return ", ".join(f"`{name}`" for name in CHECK_METHODS)


def _validate_check_class(cls: CheckClass) -> None:
    recognized = []
    for name, member in inspect.getmembers(cls, predicate=callable):
        if not name.startswith("after_"):
            continue
        if name not in CHECK_METHODS:
            raise IncorrectUsage(
                f"`{cls.__name__}` defines an unknown check method `{name}`. "
                f"Available check methods are: {_available_methods()}."
            )
        expected = _method_parameters(cast(CheckMethodName, name))
        # `member` is the unbound function from the class, so its signature includes `self`.
        parameters = list(inspect.signature(member).parameters.values())[1:]
        has_var_args = any(
            parameter.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)
            for parameter in parameters
        )
        if not has_var_args and len(parameters) != len(expected):
            names = ", ".join(expected)
            raise IncorrectUsage(
                f"`{cls.__name__}.{name}` must accept {len(expected)} arguments ({names}) but takes {len(parameters)}."
            )
        recognized.append(name)
    if not recognized:
        raise IncorrectUsage(
            f"`{cls.__name__}` does not implement any check method. Define at least one of: {_available_methods()}."
        )


def build_response_checks(response_checks: ResponseChecks) -> list[CheckFunction]:
    result: list[CheckFunction] = []
    for name, instance, lock in response_checks:
        method = instance.after_response

        def run(
            ctx: CheckContext,
            response: Response,
            case: Case,
            _method: CheckFunction = method,
            _lock: threading.Lock = lock,
        ) -> bool | None:
            # Per-instance lock so distinct checks don't serialize on one global lock.
            with _lock:
                return _method(ctx, response, case)

        run.__name__ = name
        result.append(run)
    return result


CHECKS = Registry[CheckFunction | CheckClass]()


def _instantiate_check(cls: CheckClass, config_kwargs: dict[str, Any]) -> CheckInstance:
    if not config_kwargs:
        return cast(CheckInstance, cls())
    sig = inspect.signature(cls)
    params = sig.parameters
    has_var_kw = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values())
    if has_var_kw:
        return cast(CheckInstance, cls(**config_kwargs))
    known = set(params)
    unknown = set(config_kwargs) - known
    if unknown:
        if known:
            key_word = "key" if len(known) == 1 else "keys"
            hint = f"Valid {key_word}: {', '.join(sorted(known))}."
        else:
            hint = f"{cls.__name__!r} defines no configurable __init__ parameters."
        raise ConfigError(f"Check {cls.__name__!r} does not accept config key(s): {', '.join(sorted(unknown))}. {hint}")
    return cast(CheckInstance, cls(**config_kwargs))


@dataclass(slots=True)
class RunChecks:
    """Class-based check instances, built once per run and shared across all worker threads."""

    instances: dict[str, CheckInstance]
    # Per-instance locks so distinct checks don't serialize globally.
    locks: dict[str, threading.Lock]

    @classmethod
    def from_registry(cls, config: ChecksConfig) -> RunChecks:
        # A configured section for an unknown name is a typo; CLI-selected checks carry no settings.
        registered = set(CHECKS.get_all_names())
        unknown = sorted(name for name in config.custom_kwargs if name not in registered)
        if unknown:
            raise ConfigError(
                f"Unknown check(s) in configuration: {', '.join(unknown)}. "
                "Each configured `[checks.<name>]` must match a built-in check or a registered custom check."
            )
        instances: dict[str, CheckInstance] = {}
        for item in CHECKS.get_all():
            if not is_check_class(item):
                continue
            name = item.__name__
            if not config.get_by_name(name=name).enabled:
                continue
            kwargs = config.custom_kwargs.get(name, {})
            try:
                instances[name] = _instantiate_check(item, kwargs)
            except ConfigError:
                raise
            except Exception as exc:
                raise ConfigError(f"Failed to initialize check {name!r}: {exc}") from exc
        return cls(instances=instances, locks={name: threading.Lock() for name in instances})

    def for_responses(self) -> ResponseChecks:
        return [
            ResponseCheckEntry(name, cast(ResponseCheck, instance), self.locks[name])
            for name, instance in self.instances.items()
            if implements_method(instance, "after_response")
        ]

    def for_run(self) -> list[tuple[str, RunCheck]]:
        return [
            (name, cast(RunCheck, instance))
            for name, instance in self.instances.items()
            if implements_method(instance, "after_run")
        ]


_RUN_CHECKS_LOCK = threading.Lock()


def run_checks_for(schema: BaseSchema) -> RunChecks:
    # Per-schema cache (non-engine paths) so `after_response`/`after_run` share one instance.
    # Lazy + atomic: built on first access, once, even under concurrency.
    cached = schema.__dict__.get("_run_checks")
    if cached is None:
        with _RUN_CHECKS_LOCK:
            cached = schema.__dict__.get("_run_checks")
            if cached is None:
                cached = RunChecks.from_registry(config=schema.config.checks_config_for())
                schema.__dict__["_run_checks"] = cached
    return cached


def load_all_checks() -> None:
    # NOTE: Trigger registering all Open API checks
    from schemathesis.specs.openapi.checks import status_code_conformance  # noqa: F401


def check(func: CheckFunction | CheckClass) -> CheckFunction | CheckClass:
    """Register a custom validation check.

    Args:
        func: A function `(ctx, response, case)` validating a single response, or a class with
            `after_response` and/or `after_run` methods for checks that span the whole run.

    Example:
        ```python
        import schemathesis

        @schemathesis.check
        def check_cors_headers(ctx, response, case):
            \"\"\"Verify CORS headers are present\"\"\"
            if "Access-Control-Allow-Origin" not in response.headers:
                raise AssertionError("Missing CORS headers")
        ```

    A class-based check may implement `after_response` (called per response) and/or `after_run`
    (called once after all phases). A common pattern accumulates data per response and asserts at
    the end; since `after_response` may be re-invoked during shrinking and replay, keep any such
    accumulation idempotent (use sets or min/max, not counters):

        ```python
        @schemathesis.check
        class EnsureReachability:
            \"\"\"Fail if any tested operation never returned a 2xx response during the run.\"\"\"

            def __init__(self):
                self.reached = set()
                self.tested = set()

            def after_response(self, ctx, response, case):
                label = case.operation.label
                self.tested.add(label)
                if 200 <= response.status_code < 300:
                    self.reached.add(label)

            def after_run(self, ctx):
                unreachable = self.tested - self.reached
                if unreachable:
                    raise AssertionError("never returned 2xx: " + ", ".join(sorted(unreachable)))
        ```

    If the class defines `__init__` with keyword arguments, Schemathesis forwards matching config
    values from the config file, so each check can be tuned per project without changing code:

        ```toml
        [checks.EnsureReachability]
        ignore_operations = ["POST /health"]
        ```

    """
    name = func.__name__
    if name in CHECKS.get_all_names():
        existing = CHECKS.get_one(name)
        # Same-module re-register (re-exec/reload) is fine; a different module is shadowing.
        if existing is not func and existing.__module__ != func.__module__:
            raise IncorrectUsage(f"A check named {name!r} is already registered.")
    if is_check_class(func):
        _validate_check_class(func)
    return CHECKS.register(func)


@check
def not_a_server_error(ctx: CheckContext, response: Response, case: Case) -> bool | None:
    """A check to verify that the response is not a server-side error."""
    from schemathesis.specs.openapi.utils import expand_status_codes

    expected_statuses = expand_status_codes(ctx.config.not_a_server_error.expected_statuses or [])

    status_code = response.status_code
    if status_code not in expected_statuses:
        raise ServerError(operation=case.operation.label, status_code=status_code)
    case.operation.schema.evaluate_server_error(case, response)
    return None


DEFAULT_MAX_RESPONSE_TIME = 10.0


def max_response_time(ctx: CheckContext, response: Response, case: Case) -> bool | None:
    limit = ctx.config.max_response_time.limit or DEFAULT_MAX_RESPONSE_TIME
    elapsed = response.elapsed
    if elapsed > limit:
        raise ResponseTimeExceeded(
            operation=case.operation.label,
            message=f"Actual: {elapsed * 1000:.2f}ms\nLimit: {limit * 1000:.2f}ms",
            elapsed=elapsed,
            deadline=limit,
        )
    return None


def _failures_from_exception(name: str, operation_label: str | None, exc: BaseException) -> Iterator[Failure]:
    if isinstance(exc, Failure):
        yield exc.with_traceback(None)
    elif isinstance(exc, FailureGroup):
        yield from exc.exceptions
    elif isinstance(exc, AssertionError):
        yield CustomFailure(
            operation=operation_label,
            title=f"Custom check failed: `{name}`",
            message=str(exc),
            exception=exc,
        )


def run_checks(
    *,
    case: Case,
    response: Response,
    ctx: CheckContext,
    checks: Iterable[CheckFunction],
    on_failure: Callable[[str, set[Failure], Failure], None],
    on_success: Callable[[str, Case], None] | None = None,
) -> set[Failure]:
    """Run a set of checks against a response."""
    collected: set[Failure] = set()

    for check in checks:
        name = check.__name__
        try:
            skip_check = check(ctx, response, case)
        except (Failure, AssertionError, FailureGroup) as exc:
            for failure in _failures_from_exception(name, case.operation.label, exc):
                on_failure(name, collected, failure)
        else:
            if not skip_check and on_success:
                on_success(name, case)

    return collected


def collect_after_run_failures(
    config: ProjectConfig,
    checks: list[tuple[str, RunCheck]],
    transport_kwargs: dict[str, Any] | None = None,
) -> list[Failure]:
    """Run each class-based check's `after_run` once the run has completed and collect their failures."""
    from requests.structures import CaseInsensitiveDict

    headers = config.headers_for()
    ctx = CheckContext(
        override=None,
        auth=config.auth_for(),
        headers=CaseInsensitiveDict(headers) if headers else None,
        config=config.checks_config_for(),
        transport_kwargs=transport_kwargs,
        recorder=None,
        response_checks=None,
        phase=None,
    )
    failures: list[Failure] = []
    for name, instance in checks:
        try:
            instance.after_run(ctx)
        except (Failure, AssertionError, FailureGroup) as exc:
            failures.extend(_failures_from_exception(name, None, exc))
        except Exception as exc:
            # A check bug must not crash the run; report it.
            failures.append(
                CustomFailure(
                    operation=None,
                    title=f"Check `{name}` raised an unexpected error",
                    message=f"{type(exc).__name__}: {exc}",
                    exception=exc,  # type: ignore[arg-type]
                )
            )
    return failures


def __getattr__(name: str) -> Any:
    try:
        return CHECKS.get_one(name)
    except KeyError:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None


def __dir__() -> list[str]:
    return sorted(list(globals().keys()) + CHECKS.get_all_names())
