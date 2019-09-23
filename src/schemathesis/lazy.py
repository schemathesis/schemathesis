from inspect import signature
from typing import Any, Callable, Dict, Optional

import attr
from _pytest.fixtures import FixtureRequest
from pytest_subtests import SubTests

from .schemas import BaseSchema
from .types import Filter


@attr.s(slots=True)
class LazySchema:
    fixture_name: str = attr.ib()

    def parametrize(self, filter_method: Optional[Filter] = None, filter_endpoint: Optional[Filter] = None) -> Callable:
        def wrapper(func: Callable) -> Callable:
            def test(request: FixtureRequest, subtests: SubTests) -> None:
                schema = get_schema(request, self.fixture_name, filter_method, filter_endpoint)
                fixtures = get_fixtures(func, request)
                node_id = subtests.item._nodeid
                for endpoint, sub_test in schema.get_all_tests(func):
                    subtests.item._nodeid = f"{node_id}[{endpoint.method}:{endpoint.path}]"
                    with subtests.test(method=endpoint.method, path=endpoint.path):
                        sub_test(**fixtures)
                subtests.item._nodeid = node_id

            return test

        return wrapper


def get_schema(
    request: FixtureRequest, name: str, filter_method: Optional[Filter] = None, filter_endpoint: Optional[Filter] = None
) -> BaseSchema:
    """Loads a schema from the fixture."""
    schema = request.getfixturevalue(name)
    if not isinstance(schema, BaseSchema):
        raise ValueError(f"The given schema must be an instance of BaseSchema, got: {type(schema)}")
    schema.filter_method = filter_method
    schema.filter_endpoint = filter_endpoint
    return schema


def get_fixtures(func: Callable, request: FixtureRequest) -> Dict[str, Any]:
    """Load fixtures, needed for the test function."""
    sig = signature(func)
    return {name: request.getfixturevalue(name) for name in sig.parameters if name != "case"}
