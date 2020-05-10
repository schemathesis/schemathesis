import json
from typing import Any, Dict, List

import attr

from .models import Case, Endpoint
from .utils import NOT_SET, GenericResponse


@attr.s(slots=True, hash=False)  # pragma: no mutate
class ParsedData:
    """A structure that holds information parsed from a test outcomes.

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

    def parse(self, case: Case, response: GenericResponse) -> ParsedData:
        raise NotImplementedError

    def make_endpoint(self, data: List[ParsedData]) -> Endpoint:
        raise NotImplementedError
