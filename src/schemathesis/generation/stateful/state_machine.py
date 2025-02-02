from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from typing import TYPE_CHECKING, Any, ClassVar

import hypothesis
from hypothesis.errors import InvalidDefinition
from hypothesis.stateful import RuleBasedStateMachine

from schemathesis.checks import CheckFunction
from schemathesis.core.errors import NoLinksFound
from schemathesis.core.result import Result
from schemathesis.core.transport import Response
from schemathesis.generation.case import Case

if TYPE_CHECKING:
    import hypothesis
    from requests.structures import CaseInsensitiveDict

    from schemathesis.schemas import BaseSchema


DEFAULT_STATEFUL_STEP_COUNT = 6
DEFAULT_STATE_MACHINE_SETTINGS = hypothesis.settings(
    phases=[hypothesis.Phase.generate],
    deadline=None,
    stateful_step_count=DEFAULT_STATEFUL_STEP_COUNT,
    suppress_health_check=list(hypothesis.HealthCheck),
)


@dataclass
class StepInput:
    """Input for a single state machine step."""

    case: Case
    transition: Transition | None  # None for initial steps

    __slots__ = ("case", "transition")

    @classmethod
    def initial(cls, case: Case) -> StepInput:
        return cls(case=case, transition=None)


@dataclass
class Transition:
    """Data about transition execution."""

    # ID of the transition (e.g. link name)
    id: str
    parent_id: str
    parameters: dict[str, dict[str, ExtractedParam]]
    request_body: ExtractedParam | None

    __slots__ = ("id", "parent_id", "parameters", "request_body")


@dataclass
class ExtractedParam:
    """Result of parameter extraction."""

    definition: Any
    value: Result[Any, Exception]

    __slots__ = ("definition", "value")


@dataclass
class ExtractionFailure:
    """Represents a failure to extract data from a transition."""

    # e.g., "GetUser"
    id: str
    case_id: str
    # e.g., "POST /users"
    source: str
    # e.g., "GET /users/{userId}"
    target: str
    # e.g., "userId"
    parameter_name: str
    # e.g., "$response.body#/id"
    expression: str
    # Previous test cases in the chain, from newest to oldest
    # Stored as a case + response pair
    history: list[tuple[Case, Response]]
    # The actual response that caused the failure
    response: Response
    error: Exception | None

    __slots__ = ("id", "case_id", "source", "target", "parameter_name", "expression", "history", "response", "error")

    def __eq__(self, other: object) -> bool:
        assert isinstance(other, ExtractionFailure)
        return (
            self.source == other.source
            and self.target == other.target
            and self.id == other.id
            and self.parameter_name == other.parameter_name
            and self.expression == other.expression
        )

    def __hash__(self) -> int:
        return hash(
            (
                self.source,
                self.target,
                self.id,
                self.parameter_name,
                self.expression,
            )
        )


@dataclass
class StepOutput:
    """Output from a single transition of a state machine."""

    response: Response
    case: Case

    __slots__ = ("response", "case")


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

    def __init__(self) -> None:
        try:
            super().__init__()  # type: ignore
        except InvalidDefinition as exc:
            if "defines no rules" in str(exc):
                if not self.schema.statistic.links.total:
                    message = "Schema contains no link definitions required for stateful testing"
                else:
                    message = "All link definitions required for stateful testing are excluded by filters"
                raise NoLinksFound(message) from None
            raise
        self.setup()

    @classmethod
    @lru_cache
    def _to_test_case(cls) -> type:
        from schemathesis.generation.stateful import run_state_machine_as_test

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

    def _get_target_for_result(self, result: StepOutput) -> str | None:
        raise NotImplementedError

    def _add_result_to_targets(self, targets: tuple[str, ...], result: StepOutput | None) -> None:
        if result is None:
            return
        target = self._get_target_for_result(result)
        if target is not None:
            super()._add_result_to_targets((target,), result)

    @classmethod
    def run(cls, *, settings: hypothesis.settings | None = None) -> None:
        """Run state machine as a test."""
        from . import run_state_machine_as_test

        return run_state_machine_as_test(cls, settings=settings)

    def setup(self) -> None:
        """Hook method that runs unconditionally in the beginning of each test scenario."""

    def teardown(self) -> None:
        pass

    # To provide the return type in the rendered documentation
    teardown.__doc__ = RuleBasedStateMachine.teardown.__doc__

    def _step(self, input: StepInput) -> StepOutput | None:
        __tracebackhide__ = True
        return self.step(input)

    def step(self, input: StepInput) -> StepOutput:
        """A single state machine step.

        :param Case case: Generated test case data that should be sent in an API call to the tested API operation.
        :param previous: Optional result from the previous step and the direction in which this step should be done.

        Schemathesis prepares data, makes a call and validates the received response.
        It is the most high-level point to extend the testing process. You probably don't need it in most cases.
        """
        __tracebackhide__ = True
        self.before_call(input.case)
        kwargs = self.get_call_kwargs(input.case)
        response = self.call(input.case, **kwargs)
        self.after_call(response, input.case)
        self.validate_response(response, input.case, **kwargs)
        return StepOutput(response, input.case)

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
        self, response: Response, case: Case, additional_checks: list[CheckFunction] | None = None, **kwargs: Any
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
        case.validate_response(response, additional_checks=additional_checks, transport_kwargs=kwargs)
