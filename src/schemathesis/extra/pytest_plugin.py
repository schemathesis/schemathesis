from typing import Any, Callable, Generator, List, Optional

import pytest
from _pytest import nodes
from _pytest.config import hookimpl
from _pytest.python import Function, PyCollector  # type: ignore
from hypothesis.errors import InvalidArgument  # pylint: disable=ungrouped-imports

from .._hypothesis import create_test
from ..models import Endpoint
from ..utils import is_schemathesis_test


@hookimpl(hookwrapper=True)  # type:ignore # pragma: no mutate
def pytest_pycollect_makeitem(collector: nodes.Collector, name: str, obj: Any) -> Optional["SchemathesisCase"]:
    """Switch to a different collector if the test is parametrized marked by schemathesis."""
    outcome = yield
    if is_schemathesis_test(obj):
        outcome.force_result(SchemathesisCase(obj, name, collector))
    else:
        outcome.get_result()


class SchemathesisCase(PyCollector):
    def __init__(self, test_function: Callable, *args: Any, **kwargs: Any) -> None:
        self.test_function = test_function
        self.schemathesis_case = test_function._schemathesis_test  # type: ignore
        super().__init__(*args, **kwargs)

    def _get_test_name(self, endpoint: Endpoint) -> str:
        return f"{self.name}[{endpoint.method}:{endpoint.path}]"

    def _gen_items(self, endpoint: Endpoint) -> Generator[Function, None, None]:
        """Generate all items for the given endpoint.

        Could produce more than one test item if
        parametrization is applied via ``pytest.mark.parametrize`` or ``pytest_generate_tests``.
        """
        hypothesis_item = create_test(endpoint, self.test_function)
        items = self.ihook.pytest_pycollect_makeitem(
            collector=self.parent, name=self._get_test_name(endpoint), obj=hypothesis_item
        )
        for item in items:
            item.obj = hypothesis_item
            yield item

    def collect(self) -> List[Function]:  # type: ignore
        """Generate different test items for all endpoints available in the given schema."""
        try:
            return [
                item for endpoint in self.schemathesis_case.get_all_endpoints() for item in self._gen_items(endpoint)
            ]
        except Exception:
            pytest.fail("Error during collection")


@hookimpl(hookwrapper=True)  # pragma: no mutate
def pytest_pyfunc_call(pyfuncitem):  # type:ignore
    """It is possible to have a Hypothesis exception in runtime.

    For example - kwargs validation is failed for some strategy.
    """
    outcome = yield
    try:
        outcome.get_result()
    except InvalidArgument as exc:
        pytest.fail(exc.args[0])
