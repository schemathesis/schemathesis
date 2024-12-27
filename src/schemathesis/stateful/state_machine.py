from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from typing import TYPE_CHECKING, Any, ClassVar

import hypothesis
from hypothesis.errors import InvalidDefinition
from hypothesis.stateful import RuleBasedStateMachine

from schemathesis.checks import CheckFunction
from schemathesis.core.errors import IncorrectUsage
from schemathesis.core.transport import Response
from schemathesis.generation.case import Case
from schemathesis.stateful.graph import ExecutionGraph, ExecutionMetadata

if TYPE_CHECKING:
    import hypothesis
    from requests.structures import CaseInsensitiveDict

    from schemathesis.schemas import APIOperation, BaseSchema

    from .statistic import TransitionStats

NO_LINKS_ERROR_MESSAGE = (
    "Stateful testing requires at least one OpenAPI link in the schema, but no links detected. "
    "Please add OpenAPI links to enable stateful testing or use stateless tests instead. \n"
    "See https://schemathesis.readthedocs.io/en/stable/stateful.html#how-to-specify-connections for more information."
)

DEFAULT_STATE_MACHINE_SETTINGS = hypothesis.settings(
    phases=[hypothesis.Phase.generate],
    deadline=None,
    stateful_step_count=6,
    suppress_health_check=list(hypothesis.HealthCheck),
)


@dataclass
class StepResult:
    """Output from a single transition of a state machine."""

    response: Response
    case: Case


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
                raise IncorrectUsage(NO_LINKS_ERROR_MESSAGE) from None
            raise
        self.setup()

    @classmethod
    @lru_cache
    def _to_test_case(cls) -> type:
        from . import run_state_machine_as_test

        class StateMachineTestCase(RuleBasedStateMachine.TestCase):
            settings = DEFAULT_STATE_MACHINE_SETTINGS

            def runTest(self) -> None:
                run_state_machine_as_test(cls, settings=self.settings)

            runTest.is_hypothesis_test = True  # type: ignore[attr-defined]

        StateMachineTestCase.__name__ = cls.__name__ + ".TestCase"
        StateMachineTestCase.__qualname__ = cls.__qualname__ + ".TestCase"
        return StateMachineTestCase

    def _new_name(self, target: str) -> str:
        target = _normalize_name(target)
        return super()._new_name(target)  # type: ignore

    def _get_target_for_result(self, result: StepResult) -> str | None:
        raise NotImplementedError

    def _add_result_to_targets(self, targets: tuple[str, ...], result: StepResult | None) -> None:
        if result is None:
            return
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

    def setup(self) -> None:
        """Hook method that runs unconditionally in the beginning of each test scenario."""
        self._execution_graph = ExecutionGraph()

    def teardown(self) -> None:
        pass

    # To provide the return type in the rendered documentation
    teardown.__doc__ = RuleBasedStateMachine.teardown.__doc__

    def transform(self, execution_graph: ExecutionGraph, result: StepResult, direction: Direction, case: Case) -> Case:
        raise NotImplementedError

    def _step(self, case: Case, previous: StepResult | None = None, link: Direction | None = None) -> StepResult | None:
        # This method is a proxy that is used under the hood during the state machine initialization.
        # The whole point of having it is to make it possible to override `step`; otherwise, custom "step" is ignored.
        # It happens because, at the point of initialization, the final class is not yet created.
        __tracebackhide__ = True
        if previous is not None and link is not None:
            return self.step(case, (previous, link))
        return self.step(case, None)

    def step(self, case: Case, previous: tuple[StepResult, Direction] | None = None) -> StepResult:
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
            case = self.transform(self._execution_graph, result, direction, case)
        self.before_call(case)
        kwargs = self.get_call_kwargs(case)
        response = self.call(case, **kwargs)
        if previous is None:
            self._execution_graph.add_node(
                case=case,
                metadata=ExecutionMetadata(response=response, overrides_all_parameters=False, transition_id=None),
            )
        self.after_call(response, case)
        self.validate_response(response, case, additional_checks=[use_after_free])
        return self.store_result(response, case)

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

    def after_call(self, response: Response, case: Case) -> None:
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

    def call(self, case: Case, **kwargs: Any) -> Response:
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
        self, response: Response, case: Case, additional_checks: list[CheckFunction] | None = None
    ) -> None:
        """Validate an API response.

        :param response: Response from the application under test.
        :param Case case: Generated test case data that should be sent in an API call to the tested API operation.
        :param additional_checks: A list of checks that will be run together with the default ones.
        :raises FailureGroup: If any of the supplied checks failed.

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

    def store_result(self, response: Response, case: Case) -> StepResult:
        return StepResult(response, case)


class Direction:
    name: str
    status_code: str
    operation: APIOperation

    def set_data(self, case: Case, **kwargs: Any) -> None:
        raise NotImplementedError
