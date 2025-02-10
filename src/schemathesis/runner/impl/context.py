from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ...constants import NOT_SET
from ...internal.checks import CheckConfig
from ...models import TestResult, TestResultSet

if TYPE_CHECKING:
    import threading

    from ..._override import CaseOverride
    from ...exceptions import OperationSchemaError
    from ...models import Case
    from ...types import NotSet, RawAuth


@dataclass
class RunnerContext:
    """Holds context shared for a test run."""

    data: TestResultSet
    auth: RawAuth | None
    seed: int | None
    stop_event: threading.Event
    unique_data: bool
    outcome_cache: dict[int, BaseException | None]
    checks_config: CheckConfig
    override: CaseOverride | None
    no_failfast: bool

    __slots__ = (
        "data",
        "auth",
        "seed",
        "stop_event",
        "unique_data",
        "outcome_cache",
        "checks_config",
        "override",
        "no_failfast",
    )

    def __init__(
        self,
        *,
        seed: int | None,
        auth: RawAuth | None,
        stop_event: threading.Event,
        unique_data: bool,
        checks_config: CheckConfig,
        override: CaseOverride | None,
        no_failfast: bool,
    ) -> None:
        self.data = TestResultSet(seed=seed)
        self.auth = auth
        self.seed = seed
        self.stop_event = stop_event
        self.outcome_cache = {}
        self.unique_data = unique_data
        self.checks_config = checks_config
        self.override = override
        self.no_failfast = no_failfast

    def _repr_pretty_(self, *args: Any, **kwargs: Any) -> None: ...

    @property
    def is_stopped(self) -> bool:
        return self.stop_event.is_set()

    def add_result(self, result: TestResult) -> None:
        self.data.append(result)

    def add_generic_error(self, error: OperationSchemaError) -> None:
        self.data.generic_errors.append(error)

    def add_warning(self, message: str) -> None:
        self.data.add_warning(message)

    def cache_outcome(self, case: Case, outcome: BaseException | None) -> None:
        self.outcome_cache[hash(case)] = outcome

    def get_cached_outcome(self, case: Case) -> BaseException | None | NotSet:
        return self.outcome_cache.get(hash(case), NOT_SET)


ALL_NOT_FOUND_WARNING_MESSAGE = "All API responses have a 404 status code. Did you specify the proper API location?"
