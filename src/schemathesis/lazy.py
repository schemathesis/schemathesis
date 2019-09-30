from inspect import signature
from typing import Any, Callable, Dict, Optional

import attr
from _pytest.fixtures import FixtureRequest
from pytest_subtests import SubTests

from .models import Endpoint
from .schemas import BaseSchema
from .types import Filter
from .utils import NOT_SET


@attr.s(slots=True)
class LazySchema:
    fixture_name: str = attr.ib()

    def parametrize(self, method: Optional[Filter] = NOT_SET, endpoint: Optional[Filter] = NOT_SET) -> Callable:
        def wrapper(func: Callable) -> Callable:
            def test(request: FixtureRequest, subtests: SubTests) -> None:
                """The actual test, which is executed by pytest."""
                schema = get_schema(request, self.fixture_name, method, endpoint)
                fixtures = get_fixtures(func, request)
                # Changing the node id is required for better reporting - the method and endpoint will appear there
                node_id = subtests.item._nodeid
                settings = getattr(test, "_hypothesis_internal_use_settings", None)
                for _endpoint, sub_test in schema.get_all_tests(func, settings):
                    subtests.item._nodeid = _get_node_name(node_id, _endpoint)
                    run_subtest(_endpoint, fixtures, sub_test, subtests)
                subtests.item._nodeid = node_id

            # Needed to prevent a failure when settings are applied to the test function
            test.is_hypothesis_test = True  # type: ignore

            return test

        return wrapper


def _get_node_name(node_id: str, endpoint: Endpoint) -> str:
    """Make a test node name. For example: test_api[GET:/v1/users]."""
    return f"{node_id}[{endpoint.method}:{endpoint.path}]"


def run_subtest(_endpoint: Endpoint, fixtures: Dict[str, Any], sub_test: Callable, subtests: SubTests) -> None:
    """Run the given subtest with pytest fixtures."""
    with subtests.test(method=_endpoint.method, path=_endpoint.path):
        sub_test(**fixtures)


def get_schema(
    request: FixtureRequest, name: str, method: Optional[Filter] = None, endpoint: Optional[Filter] = None
) -> BaseSchema:
    """Loads a schema from the fixture."""
    schema = request.getfixturevalue(name)
    if not isinstance(schema, BaseSchema):
        raise ValueError(f"The given schema must be an instance of BaseSchema, got: {type(schema)}")
    if method is NOT_SET:
        method = schema.method
    if method is not NOT_SET:
        endpoint = schema.endpoint
    return schema.__class__(schema.raw_schema, method=method, endpoint=endpoint)


def get_fixtures(func: Callable, request: FixtureRequest) -> Dict[str, Any]:
    """Load fixtures, needed for the test function."""
    sig = signature(func)
    return {name: request.getfixturevalue(name) for name in sig.parameters if name != "case"}
