"""GraphQL state machine."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

from hypothesis.stateful import Bundle, RuleBasedStateMachine

from schemathesis.core.errors import NoProducers
from schemathesis.core.transport import Response
from schemathesis.engine.recorder import ScenarioRecorder
from schemathesis.generation.case import Case
from schemathesis.generation.stateful import STATEFUL_TESTS_LABEL
from schemathesis.generation.stateful.control import TransitionController
from schemathesis.generation.stateful.state_machine import (
    APIStateMachine,
    StepInput,
    StepOutput,
    Transition,
)
from schemathesis.specs.graphql.stateful._rules import generate_rules_for

if TYPE_CHECKING:
    from schemathesis.checks import CheckFunction
    from schemathesis.specs.graphql.schemas import GraphQLSchema
    from schemathesis.specs.graphql.stateful._transitions import GraphQLTransitions


class GraphQLStateMachine(APIStateMachine):
    """State machine for GraphQL stateful testing built around producer/consumer chains."""

    schema: GraphQLSchema  # set on the dynamically-built subclass
    _transitions: ClassVar[GraphQLTransitions]
    # Names of `@rule` methods that populate a bundle. Empty -> stateful testing has nothing to do.
    _producer_rule_names: ClassVar[frozenset[str]] = frozenset()

    def __init__(self) -> None:
        if not type(self)._producer_rule_names:
            raise NoProducers(
                "GraphQL stateful testing requires at least one producer mutation "
                "(a mutation returning an Object type with an `id` field)."
            )
        self.recorder = ScenarioRecorder(label=STATEFUL_TESTS_LABEL)
        self._id_origins: dict[tuple[str, str], str] = {}
        self._deleted_id_origins: dict[tuple[str, str], str] = {}
        self.control = TransitionController(self._transitions)
        super().__init__()

    def after_call(self, response: Response, case: Case) -> None:
        # Always record responses so stateful checks can look up prior responses.
        self.recorder.record_response(case_id=case.id, response=response)

    def validate_response(
        self,
        response: Response,
        case: Case,
        additional_checks: list[CheckFunction] | None = None,
        **kwargs: Any,
    ) -> None:
        __tracebackhide__ = True
        case.validate_response(
            response,
            additional_checks=additional_checks,
            transport_kwargs=kwargs or None,
            recorder=self.recorder,
        )

    def _get_target_for_result(self, result: StepOutput) -> str | None:
        # Routing is driven by each rule's declared `target=` Bundle, applied via the
        # `_add_result_to_targets` override below. The base-class lookup is unused.
        return None

    def _add_result_to_targets(self, targets: tuple[str, ...], result: Any) -> None:
        RuleBasedStateMachine._add_result_to_targets(self, targets, result)

    def _add_results_to_targets(self, targets: tuple[str, ...], results: list[Any]) -> None:
        RuleBasedStateMachine._add_results_to_targets(self, targets, results)

    def _run_case(
        self,
        case: Case,
        *,
        rule_name: str,
        parent_id: str | None,
        applied_parameters: list[str] | None,
    ) -> StepOutput:
        __tracebackhide__ = True
        if parent_id is not None:
            transition: Transition | None = Transition(
                id=rule_name,
                parent_id=parent_id,
                is_inferred=False,
                parameters={},
                request_body=None,
            )
            applied = applied_parameters or []
            step_input = StepInput(case=case, transition=transition, applied_parameters=applied)
        else:
            transition = None
            step_input = StepInput.initial(case)
        self.recorder.record_case(
            parent_id=parent_id,
            case=case,
            transition=transition,
            is_transition_applied=bool(applied_parameters),
        )
        self.control.record_step(step_input, self.recorder)
        output = APIStateMachine._step(self, input=step_input)
        # `_step` only returns None when an upstream mutation produced no usable result; in
        # GraphQL that path is unreachable because every step input wraps a freshly drawn Case.
        assert output is not None
        return output


def create_state_machine(schema: GraphQLSchema) -> type[GraphQLStateMachine]:
    """Build a `GraphQLStateMachine` subclass with one bundle per id-typed object type and per-operation rules."""
    analysis = schema.analysis
    attrs: dict[str, Any] = {"schema": schema, "_transitions": analysis.transitions}
    for type_name in analysis.bundle_types:
        attrs[f"{type_name}_ids"] = Bundle(f"{type_name}_ids")
        attrs[f"deleted_{type_name}_ids"] = Bundle(f"deleted_{type_name}_ids")
    rule_set = generate_rules_for(analysis.summaries, attrs)
    for name, decorated in rule_set.rules:
        attrs[name] = decorated
    attrs["_producer_rule_names"] = rule_set.producer_names
    return type("GraphQLStateMachine", (GraphQLStateMachine,), attrs)


__all__ = ["GraphQLStateMachine", "create_state_machine"]
