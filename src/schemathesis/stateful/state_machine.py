from __future__ import annotations

import re
import time
from dataclasses import dataclass
from functools import lru_cache
from typing import TYPE_CHECKING, Any, ClassVar, Type

from hypothesis.errors import InvalidDefinition
from hypothesis.stateful import RuleBasedStateMachine

from .._dependency_versions import HYPOTHESIS_HAS_STATEFUL_NAMING_IMPROVEMENTS
from ..constants import NO_LINKS_ERROR_MESSAGE, NOT_SET
from ..exceptions import UsageError
from ..models import APIOperation, Case, CheckFunction
from .config import _default_hypothesis_settings_factory
from .runner import StatefulTestRunner, StatefulTestRunnerConfig
from .sink import StateMachineSink
from .statistic import TransitionStats

if TYPE_CHECKING:
    import hypothesis
    from requests.structures import CaseInsensitiveDict

    from ..schemas import BaseSchema
    from ..transports.responses import GenericResponse


@dataclass
class StepResult:
    """Output from a single transition of a state machine."""

    response: GenericResponse
    case: Case
    elapsed: float


def _normalize_name(name: str) -> str:
    return re.sub(r"\W|^(?=\d)", "_", name).replace("__", "_")


class APIStateMachine(RuleBasedStateMachine):
    """The base class for state machines generated from API schemas.

    Exposes additional extension points in the testing process.
    """

    # This is a convenience attribute, which happened to clash with `RuleBasedStateMachine` instance level attribute
    # They don't interfere, since it is properly overridden on the Hypothesis side, but it is likely that this
    # attribute will be renamed in the future
    bundles: ClassVar[dict[str, CaseInsensitiveDict]]  # type: ignore
    schema: BaseSchema
    # A template for transition statistics that can be filled with data from the state machine during its execution
    _transition_stats_template: ClassVar[TransitionStats]

    def __init__(self) -> None:
        try:
            super().__init__()  # type: ignore
        except InvalidDefinition as exc:
            if "defines no rules" in str(exc):
                raise UsageError(NO_LINKS_ERROR_MESSAGE) from None
            raise
        self.setup()

    @classmethod
    @lru_cache
    def _to_test_case(cls) -> Type:
        from . import run_state_machine_as_test

        class StateMachineTestCase(RuleBasedStateMachine.TestCase):
            settings = _default_hypothesis_settings_factory()

            def runTest(self) -> None:
                run_state_machine_as_test(cls, settings=self.settings)

            runTest.is_hypothesis_test = True  # type: ignore[attr-defined]

        StateMachineTestCase.__name__ = cls.__name__ + ".TestCase"
        StateMachineTestCase.__qualname__ = cls.__qualname__ + ".TestCase"
        return StateMachineTestCase

    def _pretty_print(self, value: Any) -> str:
        if isinstance(value, Case):
            # State machines suppose to be reproducible, hence it is OK to get kwargs here
            kwargs = self.get_call_kwargs(value)
            return _print_case(value, kwargs)
        return super()._pretty_print(value)  # type: ignore

    if HYPOTHESIS_HAS_STATEFUL_NAMING_IMPROVEMENTS:

        def _new_name(self, target: str) -> str:
            target = _normalize_name(target)
            return super()._new_name(target)  # type: ignore

    def _get_target_for_result(self, result: StepResult) -> str | None:
        raise NotImplementedError

    def _add_result_to_targets(self, targets: tuple[str, ...], result: StepResult | None) -> None:
        if result is None:
            return None
        target = self._get_target_for_result(result)
        if target is not None:
            super()._add_result_to_targets((target,), result)

    @classmethod
    def format_rules(cls) -> str:
        raise NotImplementedError

    @classmethod
    def run(cls, *, settings: hypothesis.settings | None = None) -> None:
        """Run state machine as a test."""
        from . import run_state_machine_as_test

        return run_state_machine_as_test(cls, settings=settings)

    @classmethod
    def runner(cls, *, config: StatefulTestRunnerConfig | None = None) -> StatefulTestRunner:
        """Create a runner for this state machine."""
        from .runner import StatefulTestRunnerConfig

        return StatefulTestRunner(cls, config=config or StatefulTestRunnerConfig())

    @classmethod
    def sink(cls) -> StateMachineSink:
        """Create a sink to collect events into."""
        return StateMachineSink(transitions=cls._transition_stats_template.copy())

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

    def _step(self, case: Case, previous: StepResult | None = None, link: Direction | None = None) -> StepResult | None:
        # This method is a proxy that is used under the hood during the state machine initialization.
        # The whole point of having it is to make it possible to override `step`; otherwise, custom "step" is ignored.
        # It happens because, at the point of initialization, the final class is not yet created.
        __tracebackhide__ = True
        if previous is not None and link is not None:
            return self.step(case, (previous, link))
        return self.step(case, None)

    def step(self, case: Case, previous: tuple[StepResult, Direction] | None = None) -> StepResult | None:
        """A single state machine step.

        :param Case case: Generated test case data that should be sent in an API call to the tested API operation.
        :param previous: Optional result from the previous step and the direction in which this step should be done.

        Schemathesis prepares data, makes a call and validates the received response.
        It is the most high-level point to extend the testing process. You probably don't need it in most cases.
        """
        from ..specs.openapi.checks import use_after_free

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
        self.validate_response(response, case, additional_checks=(use_after_free,))
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
        detection the state machine will call the ``call`` method.

        Usually, you don't need to override this method unless you are building a different state machine on top of this
        one and want to customize the transport layer itself.
        """
        return case.call(**kwargs)

    def get_call_kwargs(self, case: Case) -> dict[str, Any]:
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

    def validate_response(
        self, response: GenericResponse, case: Case, additional_checks: tuple[CheckFunction, ...] = ()
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


def _print_case(case: Case, kwargs: dict[str, Any]) -> str:
    from requests.structures import CaseInsensitiveDict

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


class Direction:
    name: str
    status_code: str
    operation: APIOperation

    def set_data(self, case: Case, elapsed: float, **kwargs: Any) -> None:
        raise NotImplementedError
