import enum
import json
from typing import Any, Callable, Dict, Generator, List, Optional, Tuple, Union

import attr
import hypothesis

from .exceptions import InvalidSchema
from .protocols import CaseProtocol, EndpointProtocol
from .utils import NOT_SET, GenericResponse


class Stateful(enum.Enum):
    links = 1


@attr.s(slots=True, hash=False)  # pragma: no mutate
class ParsedData:
    """A structure that holds information parsed from a test outcome.

    It is used later to create a new version of an endpoint that will reuse this data.
    """

    parameters: Dict[str, Any] = attr.ib()  # pragma: no mutate
    body: Any = attr.ib(default=NOT_SET)  # pragma: no mutate

    def __hash__(self) -> int:
        """Custom hash simplifies deduplication of parsed data."""
        value = hash(tuple(self.parameters.items()))  # parameters never contain nested dicts / lists
        if self.body is not NOT_SET:
            if isinstance(self.body, (dict, list)):
                # The simplest way to get a hash of a potentially nested structure
                value ^= hash(json.dumps(self.body))
            else:
                # These types should be hashable
                value ^= hash(self.body)
        return value


@attr.s(slots=True)  # pragma: no mutate
class StatefulTest:
    """A template for a test that will be executed after another one by reusing the outcomes from it."""

    name: str = attr.ib()  # pragma: no mutate

    def parse(self, case: CaseProtocol, response: GenericResponse) -> ParsedData:
        raise NotImplementedError

    def make_endpoint(self, data: List[ParsedData]) -> EndpointProtocol:
        raise NotImplementedError


@attr.s(slots=True)  # pragma: no mutate
class StatefulData:
    """Storage for data that will be used in later tests."""

    stateful_test: StatefulTest = attr.ib()  # pragma: no mutate
    container: List[ParsedData] = attr.ib(factory=list)  # pragma: no mutate

    def make_endpoint(self) -> EndpointProtocol:
        return self.stateful_test.make_endpoint(self.container)

    def store(self, case: CaseProtocol, response: GenericResponse) -> None:
        """Parse and store data for a stateful test."""
        parsed = self.stateful_test.parse(case, response)
        self.container.append(parsed)


@attr.s(slots=True)  # pragma: no mutate
class Feedback:
    """Handler for feedback from tests.

    Provides a way to control runner's behavior from tests.
    """

    stateful: Optional[Stateful] = attr.ib()  # pragma: no mutate
    endpoint: EndpointProtocol = attr.ib()  # pragma: no mutate
    stateful_tests: Dict[str, StatefulData] = attr.ib(factory=dict)  # pragma: no mutate

    def add_test_case(self, case: CaseProtocol, response: GenericResponse) -> None:
        """Store test data to reuse it in the future additional tests."""
        for stateful_test in case.endpoint.get_stateful_tests(response, self.stateful):
            data = self.stateful_tests.setdefault(stateful_test.name, StatefulData(stateful_test))
            data.store(case, response)

    def get_stateful_tests(
        self, test: Callable, settings: Optional[hypothesis.settings], seed: Optional[int]
    ) -> Generator[Tuple[EndpointProtocol, Union[Callable, InvalidSchema]], None, None]:
        """Generate additional tests that use data from the previous ones."""
        from ._hypothesis import make_test_or_exception  # pylint: disable=import-outside-toplevel

        for data in self.stateful_tests.values():
            endpoint = data.make_endpoint()
            yield endpoint, make_test_or_exception(endpoint, test, settings, seed)
