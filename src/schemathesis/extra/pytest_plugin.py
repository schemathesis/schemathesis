from functools import partial
from typing import Any, Callable, Generator, List, Optional, Type, TypeVar, cast

import pytest
from _pytest import fixtures, nodes
from _pytest.config import hookimpl
from _pytest.fixtures import FuncFixtureInfo
from _pytest.nodes import Node
from _pytest.python import Class, Function, FunctionDefinition, Metafunc, Module, PyCollector
from _pytest.runner import runtestprotocol
from _pytest.warning_types import PytestWarning
from hypothesis.errors import InvalidArgument
from packaging import version

from .. import DataGenerationMethod
from .._hypothesis import create_test
from ..models import Endpoint
from ..stateful import Feedback
from ..utils import is_schemathesis_test

USE_FROM_PARENT = version.parse(pytest.__version__) >= version.parse("5.4.0")

T = TypeVar("T", bound=Node)


def create(cls: Type[T], *args: Any, **kwargs: Any) -> T:
    if USE_FROM_PARENT:
        return cls.from_parent(*args, **kwargs)  # type: ignore
    return cls(*args, **kwargs)


class SchemathesisFunction(Function):  # pylint: disable=too-many-ancestors
    def __init__(
        self,
        *args: Any,
        test_func: Callable,
        test_name: Optional[str] = None,
        recursion_level: int = 0,
        data_generation_method: DataGenerationMethod,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.test_function = test_func
        self.test_name = test_name
        self.recursion_level = recursion_level
        self.data_generation_method = data_generation_method

    def _getobj(self) -> partial:
        """Tests defined as methods require `self` as the first argument.

        This method is called only for this case.
        """
        return partial(self.obj, self.parent.obj)  # type: ignore

    @property
    def feedback(self) -> Optional[Feedback]:
        return getattr(self.obj, "_schemathesis_feedback", None)

    def warn_if_stateful_responses_not_stored(self) -> None:
        feedback = self.feedback
        if feedback is not None and not feedback.stateful_tests:
            self.warn(PytestWarning(NOT_USED_STATEFUL_TESTING_MESSAGE))

    def _get_stateful_tests(self) -> List["SchemathesisFunction"]:
        feedback = self.feedback
        recursion_level = self.recursion_level
        if feedback is None or recursion_level >= feedback.endpoint.schema.stateful_recursion_limit:
            return []
        previous_test_name = self.test_name or f"{feedback.endpoint.method.upper()}:{feedback.endpoint.full_path}"

        def make_test(
            endpoint: Endpoint,
            test: Callable,
            data_generation_method: DataGenerationMethod,
            previous_tests: str,
        ) -> "SchemathesisFunction":
            test_name = f"{previous_tests} -> {endpoint.method.upper()}:{endpoint.full_path}"
            return create(
                self.__class__,
                name=f"{self.originalname}[{test_name}][{self.data_generation_method.as_short_name()}]",
                parent=self.parent,
                callspec=getattr(self, "callspec", None),
                callobj=test,
                fixtureinfo=self._fixtureinfo,
                keywords=self.keywords,
                originalname=self.originalname,
                test_func=self.test_function,
                test_name=test_name,
                recursion_level=recursion_level + 1,
                data_generation_method=data_generation_method,
            )

        return [
            make_test(endpoint, test, data_generation_method, previous_test_name)
            for (endpoint, data_generation_method, test) in feedback.get_stateful_tests(self.test_function, None, None)
        ]

    def add_stateful_tests(self) -> None:
        idx = self.session.items.index(self) + 1
        tests = self._get_stateful_tests()
        self.session.items[idx:idx] = tests
        self.session.testscollected += len(tests)


class SchemathesisCase(PyCollector):
    def __init__(self, test_function: Callable, *args: Any, **kwargs: Any) -> None:
        self.test_function = test_function
        self.schemathesis_case = test_function._schemathesis_test  # type: ignore
        self.given_args = getattr(test_function, "_schemathesis_given_args", ())
        self.given_kwargs = getattr(test_function, "_schemathesis_given_kwargs", {})
        super().__init__(*args, **kwargs)

    def _get_test_name(self, endpoint: Endpoint, data_generation_method: DataGenerationMethod) -> str:
        return f"{self.name}[{endpoint.method.upper()}:{endpoint.full_path}][{data_generation_method.as_short_name()}]"

    def _gen_items(
        self, endpoint: Endpoint, data_generation_method: DataGenerationMethod
    ) -> Generator[SchemathesisFunction, None, None]:
        """Generate all items for the given endpoint.

        Could produce more than one test item if
        parametrization is applied via ``pytest.mark.parametrize`` or ``pytest_generate_tests``.

        This implementation is based on the original one in pytest, but with slight adjustments
        to produce tests out of hypothesis ones.
        """
        name = self._get_test_name(endpoint, data_generation_method)
        funcobj = create_test(
            endpoint=endpoint,
            test=self.test_function,
            _given_args=self.given_args,
            _given_kwargs=self.given_kwargs,
            data_generation_method=data_generation_method,
        )

        cls = self._get_class_parent()
        definition: FunctionDefinition = create(FunctionDefinition, name=self.name, parent=self.parent, callobj=funcobj)
        fixturemanager = self.session._fixturemanager
        fixtureinfo = fixturemanager.getfixtureinfo(definition, funcobj, cls)

        metafunc = self._parametrize(cls, definition, fixtureinfo)

        if not metafunc._calls:
            yield create(
                SchemathesisFunction,
                name=name,
                parent=self.parent,
                callobj=funcobj,
                fixtureinfo=fixtureinfo,
                test_func=self.test_function,
                originalname=self.name,
                data_generation_method=data_generation_method,
            )
        else:
            fixtures.add_funcarg_pseudo_fixture_def(self.parent, metafunc, fixturemanager)
            fixtureinfo.prune_dependency_tree()
            for callspec in metafunc._calls:
                subname = f"{name}[{callspec.id}]"
                yield create(
                    SchemathesisFunction,
                    name=subname,
                    parent=self.parent,
                    callspec=callspec,
                    callobj=funcobj,
                    fixtureinfo=fixtureinfo,
                    keywords={callspec.id: True},
                    originalname=name,
                    test_func=self.test_function,
                    data_generation_method=data_generation_method,
                )

    def _get_class_parent(self) -> Optional[Type]:
        clscol = self.getparent(Class)
        return clscol.obj if clscol else None

    def _parametrize(
        self, cls: Optional[Type], definition: FunctionDefinition, fixtureinfo: FuncFixtureInfo
    ) -> Metafunc:
        parent = self.getparent(Module)
        module = parent.obj if parent is not None else parent
        metafunc = Metafunc(definition, fixtureinfo, self.config, cls=cls, module=module)
        methods = []
        if hasattr(module, "pytest_generate_tests"):
            methods.append(module.pytest_generate_tests)
        if hasattr(cls, "pytest_generate_tests"):
            cls = cast(Type, cls)
            methods.append(cls().pytest_generate_tests)
        self.ihook.pytest_generate_tests.call_extra(methods, {"metafunc": metafunc})
        return metafunc

    def collect(self) -> List[Function]:  # type: ignore
        """Generate different test items for all endpoints available in the given schema."""
        try:
            return [
                item
                for data_generation_method in self.schemathesis_case.data_generation_methods
                for endpoint in self.schemathesis_case.get_all_endpoints()
                for item in self._gen_items(endpoint, data_generation_method)
            ]
        except Exception:
            pytest.fail("Error during collection")


NOT_USED_STATEFUL_TESTING_MESSAGE = (
    "You are using stateful testing, but no responses were stored during the test! "
    "Please, use `case.call` or `case.store_response` in your test to enable stateful tests."
)


@hookimpl(hookwrapper=True)  # type:ignore # pragma: no mutate
def pytest_pycollect_makeitem(collector: nodes.Collector, name: str, obj: Any) -> Generator[None, Any, None]:
    """Switch to a different collector if the test is parametrized marked by schemathesis."""
    outcome = yield
    if is_schemathesis_test(obj):
        outcome.force_result(create(SchemathesisCase, parent=collector, test_function=obj, name=name))
    else:
        outcome.get_result()


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


def pytest_runtest_protocol(item: Function, nextitem: Optional[Function]) -> bool:
    item.ihook.pytest_runtest_logstart(nodeid=item.nodeid, location=item.location)
    reports = runtestprotocol(item, nextitem=nextitem)
    item.ihook.pytest_runtest_logfinish(nodeid=item.nodeid, location=item.location)
    if isinstance(item, SchemathesisFunction):
        for report in reports:
            if report.when == "call" and report.outcome == "passed":
                item.warn_if_stateful_responses_not_stored()
        item.add_stateful_tests()
    return True
