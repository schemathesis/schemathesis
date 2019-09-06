from typing import Any, Callable, Generator, List, Optional

import pytest
from _pytest import nodes
from _pytest.config import hookimpl
from _pytest.python import Function, PyCollector  # type: ignore
from hypothesis.errors import InvalidArgument  # pylint: disable=ungrouped-imports

from ..generator import create_hypothesis_test
from ..parametrizer import is_schemathesis_test
from ..schemas import Endpoint


def pytest_pycollect_makeitem(collector: nodes.Collector, name: str, obj: Any) -> Optional["SchemathesisCase"]:
    """Switch to a different collector if the test is wrapped with `Parametrizer.parametrize()`."""
    if is_schemathesis_test(obj):
        return SchemathesisCase(obj, name, collector)
    return None


class SchemathesisCase(PyCollector):
    def __init__(self, test_function: Callable, *args: Any, **kwargs: Any) -> None:
        self.test_function = test_function
        self.schemathesis_case = test_function._schema_parametrizer  # type: ignore
        super().__init__(*args, **kwargs)

    def _get_test_name(self, endpoint: Endpoint) -> str:
        return self.name + f"[{endpoint.method}:{endpoint.path}]"

    def _gen_items(self, endpoint: Endpoint) -> Generator[Function, None, None]:
        """Generate all items for the given endpoint.

        Could produce more than one test item if
        parametrization is applied via ``pytest.mark.parametrize`` or ``pytest_generate_tests``.
        """
        # TODO. exclude it early, if tests are deselected, then hypothesis strategies will not be used anyway
        # But, it is important to know the total number of tests for all method/endpoint combos
        hypothesis_item = create_hypothesis_test(endpoint, self.test_function)
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
                item
                for endpoint in self.schemathesis_case.schema.get_all_endpoints(
                    filter_method=self.schemathesis_case.filter_method,
                    filter_endpoint=self.schemathesis_case.filter_endpoint,
                )
                for item in self._gen_items(endpoint)
            ]
        except Exception:
            pytest.fail("Error during collection")


@hookimpl(hookwrapper=True)
def pytest_pyfunc_call(pyfuncitem):  # type:ignore
    """It is possible to have a Hypothesis exception in runtime.

    For example - kwargs validation is failed for some strategy.
    """
    outcome = yield
    try:
        outcome.get_result()
    except InvalidArgument as exc:
        pytest.fail(exc.args[0])
