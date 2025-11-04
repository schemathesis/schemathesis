from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from typing import TYPE_CHECKING, Any, ClassVar

import hypothesis
from hypothesis.errors import InvalidDefinition
from hypothesis.stateful import RuleBasedStateMachine

from schemathesis.checks import CheckFunction
from schemathesis.core import DEFAULT_STATEFUL_STEP_COUNT
from schemathesis.core.errors import STATEFUL_TESTING_GUIDE_URL, NoLinksFound
from schemathesis.core.result import Result
from schemathesis.core.transport import Response
from schemathesis.generation.case import Case

if TYPE_CHECKING:
    import hypothesis
    from requests.structures import CaseInsensitiveDict

    from schemathesis.schemas import BaseSchema


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
    # What parameters were actually applied
    # Data extraction failures can prevent it, as well as transitions can be skipped in some cases
    # to improve discovery of bugs triggered by non-stateful inputs during stateful testing
    applied_parameters: list[str]

    __slots__ = ("case", "transition", "applied_parameters")

    @classmethod
    def initial(cls, case: Case) -> StepInput:
        return cls(case=case, transition=None, applied_parameters=[])

    @property
    def is_applied(self) -> bool:
        # If the transition has no parameters or body, count it as applied
        if self.transition is not None and not self.transition.parameters and self.transition.request_body is None:
            return True
        return bool(self.applied_parameters)


@dataclass
class Transition:
    """Data about transition execution."""

    # ID of the transition (e.g. link name)
    id: str
    parent_id: str
    is_inferred: bool
    parameters: dict[str, dict[str, ExtractedParam]]
    request_body: ExtractedParam | None

    __slots__ = ("id", "parent_id", "is_inferred", "parameters", "request_body")


@dataclass
class ExtractedParam:
    """Result of parameter extraction."""

    definition: Any
    value: Result[Any, Exception]
    is_required: bool

    __slots__ = ("definition", "value", "is_required")


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
    """State machine for executing API operation sequences based on OpenAPI links.

    Automatically generates test scenarios by chaining API operations according
    to their defined relationships in the schema.
    """

    # This is a convenience attribute, which happened to clash with `RuleBasedStateMachine` instance level attribute
    # They don't interfere, since it is properly overridden on the Hypothesis side, but it is likely that this
    # attribute will be renamed in the future
    bundles: ClassVar[dict[str, CaseInsensitiveDict]]
    schema: BaseSchema

    def __init__(self) -> None:
        try:
            super().__init__()
        except InvalidDefinition as exc:
            if "defines no rules" in str(exc):
                if not self.schema.statistic.links.total:
                    message = "Schema contains no link definitions required for stateful testing"
                else:
                    message = "All link definitions required for stateful testing are excluded by filters"
                message += f"\n\nLearn how to define links: {STATEFUL_TESTING_GUIDE_URL}"
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
        return super()._new_name(target)

    def _get_target_for_result(self, result: StepOutput) -> str | None:
        raise NotImplementedError

    def _add_result_to_targets(self, targets: tuple[str, ...], result: StepOutput | None) -> None:
        if result is None:
            return
        target = self._get_target_for_result(result)
        if target is not None:
            super()._add_result_to_targets((target,), result)

    def _add_results_to_targets(self, targets: tuple[str, ...], results: list[StepOutput | None]) -> None:
        # Hypothesis >6.131.15
        for result in results:
            if result is None:
                continue
            target = self._get_target_for_result(result)
            if target is not None:
                super()._add_results_to_targets((target,), [result])

    @classmethod
    def run(cls, *, settings: hypothesis.settings | None = None) -> None:
        """Execute the state machine test scenarios.

        Args:
            settings: Hypothesis settings for test execution.

        """
        from . import run_state_machine_as_test

        __tracebackhide__ = True
        return run_state_machine_as_test(cls, settings=settings)

    def setup(self) -> None:
        """Called once at the beginning of each test scenario."""

    def teardown(self) -> None:
        """Called once at the end of each test scenario."""

    # To provide the return type in the rendered documentation
    teardown.__doc__ = RuleBasedStateMachine.teardown.__doc__

    def _step(self, input: StepInput) -> StepOutput | None:
        __tracebackhide__ = True
        return self.step(input)

    def step(self, input: StepInput) -> StepOutput:
        __tracebackhide__ = True
        self.before_call(input.case)
        kwargs = self.get_call_kwargs(input.case)
        response = self.call(input.case, **kwargs)
        self.after_call(response, input.case)
        self.validate_response(response, input.case, **kwargs)
        return StepOutput(response, input.case)

    def before_call(self, case: Case) -> None:
        """Called before each API operation in the scenario.

        Args:
            case: Test case data for the operation.

        """

    def after_call(self, response: Response, case: Case) -> None:
        """Called after each API operation in the scenario.

        Args:
            response: HTTP response from the operation.
            case: Test case data that was executed.

        """

    def call(self, case: Case, **kwargs: Any) -> Response:
        return case.call(**kwargs)

    def get_call_kwargs(self, case: Case) -> dict[str, Any]:
        """Returns keyword arguments for the API call.

        Args:
            case: Test case being executed.

        Returns:
            Dictionary passed to the `case.call()` method.

        """
        return {}

    def validate_response(
        self, response: Response, case: Case, additional_checks: list[CheckFunction] | None = None, **kwargs: Any
    ) -> None:
        """Validates the API response using configured checks.

        Args:
            response: HTTP response to validate.
            case: Test case that generated the response.
            additional_checks: Extra validation functions to run.
            kwargs: Transport-level keyword arguments.

        Raises:
            FailureGroup: When validation checks fail.

        """
        __tracebackhide__ = True
        case.validate_response(response, additional_checks=additional_checks, transport_kwargs=kwargs)
