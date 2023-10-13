import enum
import json
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, ClassVar, Dict, Generator, List, Optional, Tuple, Type

import hypothesis
from hypothesis.stateful import RuleBasedStateMachine
from hypothesis.stateful import run_state_machine_as_test as _run_state_machine_as_test
from requests.structures import CaseInsensitiveDict
from starlette.applications import Starlette

from ._compat import IS_HYPOTHESIS_ABOVE_6_68_1
from .exceptions import OperationSchemaError
from .models import APIOperation, Case, CheckFunction
from .utils import NOT_SET, GenericResponse, Ok, Result

if TYPE_CHECKING:
    from .schemas import BaseSchema


@enum.unique
class Stateful(enum.Enum):
    none = 1
    links = 2


@dataclass
class ParsedData:
    """A structure that holds information parsed from a test outcome.

    It is used later to create a new version of an API operation that will reuse this data.
    """

    parameters: Dict[str, Any]
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

    def make_operation(self, collected: List[ParsedData]) -> APIOperation:
        raise NotImplementedError


@dataclass
class StatefulData:
    """Storage for data that will be used in later tests."""

    stateful_test: StatefulTest
    container: List[ParsedData] = field(default_factory=list)

    def make_operation(self) -> APIOperation:
        return self.stateful_test.make_operation(self.container)

    def store(self, case: Case, response: GenericResponse) -> None:
        """Parse and store data for a stateful test."""
        parsed = self.stateful_test.parse(case, response)
        self.container.append(parsed)


@dataclass
class Feedback:
    """Handler for feedback from tests.

    Provides a way to control runner's behavior from tests.
    """

    stateful: Optional[Stateful]
    operation: APIOperation = field(repr=False)
    stateful_tests: Dict[str, StatefulData] = field(default_factory=dict, repr=False)

    def add_test_case(self, case: Case, response: GenericResponse) -> None:
        """Store test data to reuse it in the future additional tests."""
        for stateful_test in case.operation.get_stateful_tests(response, self.stateful):
            data = self.stateful_tests.setdefault(stateful_test.name, StatefulData(stateful_test))
            data.store(case, response)

    def get_stateful_tests(
        self,
        test: Callable,
        settings: Optional[hypothesis.settings],
        seed: Optional[int],
        as_strategy_kwargs: Optional[Dict[str, Any]],
    ) -> Generator[Result[Tuple[APIOperation, Callable], OperationSchemaError], None, None]:
        """Generate additional tests that use data from the previous ones."""
        from ._hypothesis import create_test

        for data in self.stateful_tests.values():
            operation = data.make_operation()
            test_function = create_test(
                operation=operation,
                test=test,
                settings=settings,
                seed=seed,
                data_generation_methods=operation.schema.data_generation_methods,
                as_strategy_kwargs=as_strategy_kwargs,
            )
            yield Ok((operation, test_function))


@dataclass
class StepResult:
    """Output from a single transition of a state machine."""

    response: GenericResponse
    case: Case
    elapsed: float


class Direction:
    name: str
    status_code: str
    operation: APIOperation

    def set_data(self, case: Case, elapsed: float, **kwargs: Any) -> None:
        raise NotImplementedError


def _print_case(case: Case, kwargs: Dict[str, Any]) -> str:
    operation = f"state.schema['{case.operation.path}']['{case.operation.method.upper()}']"
    headers = case.headers or CaseInsensitiveDict()
    headers.update(kwargs.get("headers", {}))
    case.headers = headers
    data = [
        f"{name}={repr(getattr(case, name))}"
        for name in ("path_parameters", "headers", "cookies", "query", "body", "media_type")
        if getattr(case, name) not in (None, NOT_SET)
    ]
    return f"{operation}.make_case({', '.join(data)})"


@dataclass(repr=False)
class _DirectionWrapper:
    """Purely to avoid modification of `Direction.__repr__`."""

    direction: Direction

    def __repr__(self) -> str:
        path = self.direction.operation.path
        method = self.direction.operation.method.upper()
        return f"state.schema['{path}']['{method}'].links['{self.direction.status_code}']['{self.direction.name}']"


def run_state_machine_as_test(
    state_machine_factory: Type["APIStateMachine"], *, settings: Optional[hypothesis.settings] = None
) -> None:
    """Run a state machine as a test.

    It automatically adds the `_min_steps` argument if ``Hypothesis`` is recent enough.
    """
    if IS_HYPOTHESIS_ABOVE_6_68_1:
        # Newer Hypothesis contains an argument to set the minimum number of steps for a state machine execution
        return _run_state_machine_as_test(state_machine_factory, settings=settings, _min_steps=2)
    return _run_state_machine_as_test(state_machine_factory, settings=settings)


class APIStateMachine(RuleBasedStateMachine):
    """The base class for state machines generated from API schemas.

    Exposes additional extension points in the testing process.
    """

    # This is a convenience attribute, which happened to clash with `RuleBasedStateMachine` instance level attribute
    # They don't interfere, since it is properly overridden on the Hypothesis side, but it is likely that this
    # attribute will be renamed in the future
    bundles: ClassVar[Dict[str, CaseInsensitiveDict]]  # type: ignore
    schema: "BaseSchema"

    def __init__(self) -> None:
        super().__init__()  # type: ignore
        self.setup()

    def _pretty_print(self, value: Any) -> str:
        if isinstance(value, Case):
            # State machines suppose to be reproducible, hence it is OK to get kwargs here
            kwargs = self.get_call_kwargs(value)
            return _print_case(value, kwargs)
        if isinstance(value, tuple) and len(value) == 2:
            result, direction = value
            wrapper = _DirectionWrapper(direction)
            return super()._pretty_print((result, wrapper))  # type: ignore
        return super()._pretty_print(value)  # type: ignore

    @classmethod
    def run(cls, *, settings: Optional[hypothesis.settings] = None) -> None:
        """Run state machine as a test."""
        return run_state_machine_as_test(cls, settings=settings)

    def setup(self) -> None:
        """Hook method that runs unconditionally in the beginning of each test scenario.

        Does nothing by default.
        """

    def teardown(self) -> None:
        pass

    # To provide the return type in the rendered documentation
    teardown.__doc__ = RuleBasedStateMachine.teardown.__doc__

    def transform(self, result: StepResult, direction: Direction, case: Case) -> Case:
        raise NotImplementedError

    def _step(self, case: Case, previous: Optional[Tuple[StepResult, Direction]] = None) -> StepResult:
        # This method is a proxy that is used under the hood during the state machine initialization.
        # The whole point of having it is to make it possible to override `step`; otherwise, custom "step" is ignored.
        # It happens because, at the point of initialization, the final class is not yet created.
        __tracebackhide__ = True
        return self.step(case, previous)

    def step(self, case: Case, previous: Optional[Tuple[StepResult, Direction]] = None) -> StepResult:
        """A single state machine step.

        :param Case case: Generated test case data that should be sent in an API call to the tested API operation.
        :param previous: Optional result from the previous step and the direction in which this step should be done.

        Schemathesis prepares data, makes a call and validates the received response.
        It is the most high-level point to extend the testing process. You probably don't need it in most cases.
        """
        __tracebackhide__ = True
        if previous is not None:
            result, direction = previous
            case = self.transform(result, direction, case)
        self.before_call(case)
        kwargs = self.get_call_kwargs(case)
        start = time.monotonic()
        response = self.call(case, **kwargs)
        elapsed = time.monotonic() - start
        self.after_call(response, case)
        self.validate_response(response, case)
        return self.store_result(response, case, elapsed)

    def before_call(self, case: Case) -> None:
        """Hook method for modifying the case data before making a request.

        :param Case case: Generated test case data that should be sent in an API call to the tested API operation.

        Use it if you want to inject static data, for example,
        a query parameter that should always be used in API calls:

        .. code-block:: python

            class APIWorkflow(schema.as_state_machine()):
                def before_call(self, case):
                    case.query = case.query or {}
                    case.query["test"] = "true"

        You can also modify data only for some operations:

        .. code-block:: python

            class APIWorkflow(schema.as_state_machine()):
                def before_call(self, case):
                    if case.method == "PUT" and case.path == "/items":
                        case.body["is_fake"] = True
        """

    def after_call(self, response: GenericResponse, case: Case) -> None:
        """Hook method for additional actions with case or response instances.

        :param response: Response from the application under test.
        :param Case case: Generated test case data that should be sent in an API call to the tested API operation.

        For example, you can log all response statuses by using this hook:

        .. code-block:: python

            import logging

            logger = logging.getLogger(__file__)
            logger.setLevel(logging.INFO)


            class APIWorkflow(schema.as_state_machine()):
                def after_call(self, response, case):
                    logger.info(
                        "%s %s -> %d",
                        case.method,
                        case.path,
                        response.status_code,
                    )


            # POST /users/ -> 201
            # GET /users/{user_id} -> 200
            # PATCH /users/{user_id} -> 200
            # GET /users/{user_id} -> 200
            # PATCH /users/{user_id} -> 500
        """

    def call(self, case: Case, **kwargs: Any) -> GenericResponse:
        """Make a request to the API.

        :param Case case: Generated test case data that should be sent in an API call to the tested API operation.
        :param kwargs: Keyword arguments that will be passed to the appropriate ``case.call_*`` method.
        :return: Response from the application under test.

        Note that WSGI/ASGI applications are detected automatically in this method. Depending on the result of this
        detection the state machine will call ``call``, ``call_wsgi`` or ``call_asgi`` methods.

        Usually, you don't need to override this method unless you are building a different state machine on top of this
        one and want to customize the transport layer itself.
        """
        method = self._get_call_method(case)
        return method(**kwargs)

    def get_call_kwargs(self, case: Case) -> Dict[str, Any]:
        """Create custom keyword arguments that will be passed to the :meth:`Case.call` method.

        Mostly they are proxied to the :func:`requests.request` call.

        :param Case case: Generated test case data that should be sent in an API call to the tested API operation.

        .. code-block:: python

            class APIWorkflow(schema.as_state_machine()):
                def get_call_kwargs(self, case):
                    return {"verify": False}

        The above example disables the server's TLS certificate verification.
        """
        return {}

    def _get_call_method(self, case: Case) -> Callable:
        if case.app is not None:
            if isinstance(case.app, Starlette):
                return case.call_asgi
            return case.call_wsgi
        return case.call

    def validate_response(
        self, response: GenericResponse, case: Case, additional_checks: Tuple[CheckFunction, ...] = ()
    ) -> None:
        """Validate an API response.

        :param response: Response from the application under test.
        :param Case case: Generated test case data that should be sent in an API call to the tested API operation.
        :param additional_checks: A list of checks that will be run together with the default ones.
        :raises CheckFailed: If any of the supplied checks failed.

        If you need to change the default checks or provide custom validation rules, you can do it here.

        .. code-block:: python

            def my_check(response, case):
                ...  # some assertions


            class APIWorkflow(schema.as_state_machine()):
                def validate_response(self, response, case):
                    case.validate_response(response, checks=(my_check,))

        The state machine from the example above will execute only the ``my_check`` check instead of all
        available checks.

        Each check function should accept ``response`` as the first argument and ``case`` as the second one and raise
        ``AssertionError`` if the check fails.

        **Note** that it is preferred to pass check functions as an argument to ``case.validate_response``.
        In this case, all checks will be executed, and you'll receive a grouped exception that contains results from
        all provided checks rather than only the first encountered exception.
        """
        __tracebackhide__ = True
        case.validate_response(response, additional_checks=additional_checks)

    def store_result(self, response: GenericResponse, case: Case, elapsed: float) -> StepResult:
        return StepResult(response, case, elapsed)
