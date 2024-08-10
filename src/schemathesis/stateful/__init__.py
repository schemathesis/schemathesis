from __future__ import annotations

import enum
import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Generator

from .. import GenerationConfig
from ..constants import NOT_SET
from ..exceptions import OperationSchemaError
from ..internal.result import Ok, Result
from ..models import APIOperation, Case

if TYPE_CHECKING:
    import hypothesis

    from ..transports.responses import GenericResponse
    from .state_machine import APIStateMachine


class UnresolvableLink(Exception):
    """Raised when a link cannot be resolved."""


@enum.unique
class Stateful(enum.Enum):
    none = 1
    links = 2


@dataclass
class ParsedData:
    """A structure that holds information parsed from a test outcome.

    It is used later to create a new version of an API operation that will reuse this data.
    """

    parameters: dict[str, Any]
    body: Any = NOT_SET

    def __hash__(self) -> int:
        """Custom hash simplifies deduplication of parsed data."""
        value = hash(tuple(self.parameters.items()))  # parameters never contain nested dicts / lists
        if self.body is not NOT_SET:
            if isinstance(self.body, (dict, list)):
                # The simplest way to get a hash of a potentially nested structure
                value ^= hash(json.dumps(self.body, sort_keys=True))
            else:
                # These types should be hashable
                value ^= hash(self.body)
        return value


@dataclass
class StatefulTest:
    """A template for a test that will be executed after another one by reusing the outcomes from it."""

    name: str

    def parse(self, case: Case, response: GenericResponse) -> ParsedData:
        raise NotImplementedError

    def is_match(self) -> bool:
        raise NotImplementedError

    def make_operation(self, collected: list[ParsedData]) -> APIOperation:
        raise NotImplementedError


@dataclass
class StatefulData:
    """Storage for data that will be used in later tests."""

    stateful_test: StatefulTest
    container: list[ParsedData] = field(default_factory=list)

    def make_operation(self) -> APIOperation:
        return self.stateful_test.make_operation(self.container)

    def store(self, case: Case, response: GenericResponse) -> None:
        """Parse and store data for a stateful test."""
        try:
            parsed = self.stateful_test.parse(case, response)
            self.container.append(parsed)
        except UnresolvableLink:
            # For now, ignore if a link cannot be resolved
            pass


@dataclass
class Feedback:
    """Handler for feedback from tests.

    Provides a way to control runner's behavior from tests.
    """

    stateful: Stateful | None
    operation: APIOperation = field(repr=False)
    stateful_tests: dict[str, StatefulData] = field(default_factory=dict, repr=False)

    def add_test_case(self, case: Case, response: GenericResponse) -> None:
        """Store test data to reuse it in the future additional tests."""
        for stateful_test in case.operation.get_stateful_tests(response, self.stateful):
            data = self.stateful_tests.setdefault(stateful_test.name, StatefulData(stateful_test))
            data.store(case, response)

    def get_stateful_tests(
        self,
        test: Callable,
        settings: hypothesis.settings | None,
        generation_config: GenerationConfig | None,
        seed: int | None,
        as_strategy_kwargs: dict[str, Any] | Callable[[APIOperation], dict[str, Any]] | None,
    ) -> Generator[Result[tuple[APIOperation, Callable], OperationSchemaError], None, None]:
        """Generate additional tests that use data from the previous ones."""
        from .._hypothesis import create_test

        for data in self.stateful_tests.values():
            if data.stateful_test.is_match():
                operation = data.make_operation()
                _as_strategy_kwargs: dict[str, Any] | None
                if callable(as_strategy_kwargs):
                    _as_strategy_kwargs = as_strategy_kwargs(operation)
                else:
                    _as_strategy_kwargs = as_strategy_kwargs
                test_function = create_test(
                    operation=operation,
                    test=test,
                    settings=settings,
                    seed=seed,
                    data_generation_methods=operation.schema.data_generation_methods,
                    generation_config=generation_config,
                    as_strategy_kwargs=_as_strategy_kwargs,
                )
                yield Ok((operation, test_function))


def run_state_machine_as_test(
    state_machine_factory: type[APIStateMachine], *, settings: hypothesis.settings | None = None
) -> None:
    """Run a state machine as a test.

    It automatically adds the `_min_steps` argument if ``Hypothesis`` is recent enough.
    """
    from hypothesis.stateful import run_state_machine_as_test as _run_state_machine_as_test

    return _run_state_machine_as_test(state_machine_factory, settings=settings, _min_steps=2)
