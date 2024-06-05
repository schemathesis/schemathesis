from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, Dict, Iterator, TypedDict, Union

from hypothesis import strategies as st
from hypothesis.stateful import Bundle, Rule, precondition, rule

from ....internal.result import Ok
from ....stateful import StateMachineStatistic
from ....stateful.state_machine import APIStateMachine, Direction, StepResult
from .. import expressions
from ..links import get_all_links
from ..utils import expand_status_code

if TYPE_CHECKING:
    from ....models import Case
    from ....stateful import StateMachineConfig
    from ..schemas import BaseOpenAPISchema

FilterFunction = Callable[["StepResult"], bool]
StatusCode = str
LinkName = str
TargetName = str
SourceName = str
ResponseCounter = Dict[int, int]
AggregatedResponseCounter = TypedDict("AggregatedResponseCounter", {"2xx": int, "4xx": int, "5xx": int})


@dataclass
class LinkSource:
    name: str
    responses: dict[StatusCode, dict[TargetName, dict[LinkName, ResponseCounter]]]
    is_first: bool

    __slots__ = ("name", "responses", "is_first")


@dataclass
class OperationResponse:
    status_code: str
    targets: dict[TargetName, dict[LinkName, ResponseCounter]]
    is_last: bool

    __slots__ = ("status_code", "targets", "is_last")


@dataclass
class Link:
    name: str
    target: str
    responses: ResponseCounter
    is_last: bool
    is_single: bool

    __slots__ = ("name", "target", "responses", "is_last", "is_single")


StatisticEntry = Union[LinkSource, OperationResponse, Link]


@dataclass
class FormattedStatisticEntry:
    line: str
    entry: StatisticEntry
    __slots__ = ("line", "entry")


@dataclass
class OpenAPIStateMachineStatistic(StateMachineStatistic):
    """Statistics for a state machine run."""

    data: dict[SourceName, dict[StatusCode, dict[TargetName, dict[LinkName, ResponseCounter]]]]

    __slots__ = ("data",)

    def iter(self) -> Iterator[StatisticEntry]:
        for source_idx, (source, responses) in enumerate(self.data.items()):
            yield LinkSource(name=source, responses=responses, is_first=source_idx == 0)
            for response_idx, (status_code, targets) in enumerate(responses.items()):
                yield OperationResponse(
                    status_code=status_code, targets=targets, is_last=response_idx == len(responses) - 1
                )
                for target_idx, (target, links) in enumerate(targets.items()):
                    for link_idx, (link_name, link_responses) in enumerate(links.items()):
                        yield Link(
                            name=link_name,
                            target=target,
                            responses=link_responses,
                            is_last=target_idx == len(targets) - 1 and link_idx == len(links) - 1,
                            is_single=len(links) == 1,
                        )

    def iter_with_format(self) -> Iterator[FormattedStatisticEntry]:
        current_response = None
        for entry in self.iter():
            if isinstance(entry, LinkSource):
                if not entry.is_first:
                    yield FormattedStatisticEntry(line=f"\n{entry.name}", entry=entry)
                else:
                    yield FormattedStatisticEntry(line=f"{entry.name}", entry=entry)
            elif isinstance(entry, OperationResponse):
                current_response = entry
                if entry.is_last:
                    yield FormattedStatisticEntry(line=f"└── {entry.status_code}", entry=entry)
                else:
                    yield FormattedStatisticEntry(line=f"├── {entry.status_code}", entry=entry)
            else:
                if current_response is not None and current_response.is_last:
                    line = "    "
                else:
                    line = "│   "
                if entry.is_last:
                    line += "└"
                else:
                    line += "├"
                if entry.is_single or entry.name == entry.target:
                    line += f"── {entry.target}"
                else:
                    line += f"── {entry.name} -> {entry.target}"
                yield FormattedStatisticEntry(line=line, entry=entry)


class OpenAPIStateMachine(APIStateMachine):
    _statistic_template: OpenAPIStateMachineStatistic

    def transform(self, result: StepResult, direction: Direction, case: Case) -> Case:
        context = expressions.ExpressionContext(case=result.case, response=result.response)
        direction.set_data(case, elapsed=result.elapsed, context=context)
        return case

    @classmethod
    def format_rules(cls) -> str:
        return "\n".join(item.line for item in cls._statistic_template.iter_with_format())


def create_state_machine(
    schema: BaseOpenAPISchema, *, config: StateMachineConfig | None = None
) -> type[APIStateMachine]:
    """Create a state machine class.

    It aims to avoid making calls that are not likely to lead to a stateful call later. For example:
      1. POST /users/
      2. GET /users/{id}/

    This state machine won't make calls to (2) without having a proper response from (1) first.
    """
    operations = [result.ok() for result in schema.get_all_operations() if isinstance(result, Ok)]
    bundles = {operation.verbose_name: Bundle(operation.verbose_name) for operation in operations}
    incoming_transitions = defaultdict(list)
    # Statistic structure follows the links and count for each response status code
    statistic = {}
    for operation in operations:
        operation_links: dict[StatusCode, dict[TargetName, dict[LinkName, dict[int, int]]]] = {}
        for _, link in get_all_links(operation):
            target_operation = link.get_target_operation()
            incoming_transitions[target_operation.verbose_name].append(link)
            response_targets = operation_links.setdefault(link.status_code, {})
            target_links = response_targets.setdefault(target_operation.verbose_name, {})
            target_links[link.name] = {}
        if operation_links:
            statistic[operation.verbose_name] = operation_links
    rules = {}

    for target in operations:
        incoming = incoming_transitions.get(target.verbose_name)
        target_bundle = bundles[target.verbose_name]
        if incoming is not None:
            for link in incoming:
                source = link.operation
                all_status_codes = source.definition.raw["responses"].keys()
                predicate = make_response_filter(link.status_code, all_status_codes)
                rules[f"{source.verbose_name} -> {link.status_code} -> {target.verbose_name}"] = precondition(
                    lambda self, _predicate=predicate: self._has_matching_response(_predicate),
                )(
                    transition(
                        target=target_bundle,
                        previous=bundles[source.verbose_name].filter(predicate),
                        case=target.as_strategy(),
                        link=st.just(link),
                    )
                )
        elif any(
            incoming.operation.verbose_name == target.verbose_name
            for transitions in incoming_transitions.values()
            for incoming in transitions
        ):
            # No incoming transitions, but has at least one outgoing transition
            # For example, POST /users/ -> GET /users/{id}/
            # The source operation has no prerequisite, but we need to allow this rule to be executed
            # in order to reach other transitions
            rules[f"* -> {target.verbose_name}"] = transition(
                target=target_bundle, previous=st.none(), case=target.as_strategy()
            )

    return type(
        "APIWorkflow",
        (OpenAPIStateMachine,),
        {
            "schema": schema,
            "config": config or StateMachineConfig(),
            "bundles": bundles,
            "_statistic_template": OpenAPIStateMachineStatistic(data=statistic),
            **rules,
        },
    )


def transition(*args: Any, **kwargs: Any) -> Callable[[Callable], Rule]:
    return rule(*args, **kwargs)(APIStateMachine._step)


def make_response_filter(status_code: str, all_status_codes: Iterator[str]) -> FilterFunction:
    """Create a filter for stored responses.

    This filter will decide whether some response is suitable to use as a source for requesting some API operation.
    """
    if status_code == "default":
        return default_status_code(all_status_codes)
    return match_status_code(status_code)


def match_status_code(status_code: str) -> FilterFunction:
    """Create a filter function that matches all responses with the given status code.

    Note that the status code can contain "X", which means any digit.
    For example, 50X will match all status codes from 500 to 509.
    """
    status_codes = set(expand_status_code(status_code))

    def compare(result: StepResult) -> bool:
        return result.response.status_code in status_codes

    # This name is displayed in the resulting strategy representation. For example, if you run your tests with
    # `--hypothesis-show-statistics`, then you can see `Bundle(name='GET /users/{user_id}').filter(match_200_response)`
    # which gives you information about the particularly used filter.
    compare.__name__ = f"match_{status_code}_response"

    return compare


def default_status_code(status_codes: Iterator[str]) -> FilterFunction:
    """Create a filter that matches all "default" responses.

    In Open API, the "default" response is the one that is used if no other options were matched.
    Therefore, we need to match only responses that were not matched by other listed status codes.
    """
    expanded_status_codes = {
        status_code for value in status_codes if value != "default" for status_code in expand_status_code(value)
    }

    def match_default_response(result: StepResult) -> bool:
        return result.response.status_code not in expanded_status_codes

    return match_default_response
