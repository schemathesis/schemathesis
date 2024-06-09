from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Iterator, List, Union

from ....internal.copy import fast_deepcopy
from ....stateful.statistic import TransitionStats
from .types import AggregatedResponseCounter, LinkName, ResponseCounter, SourceName, StatusCode, TargetName

if TYPE_CHECKING:
    from ....stateful import events


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
class OpenAPILinkStats(TransitionStats):
    """Statistics about link transitions for a state machine run."""

    transitions: dict[SourceName, dict[StatusCode, dict[TargetName, dict[LinkName, ResponseCounter]]]]

    roots: dict[TargetName, ResponseCounter] = field(default_factory=dict)

    __slots__ = ("transitions",)

    def consume(self, event: events.StatefulEvent) -> None:
        from ....stateful import events

        if isinstance(event, events.StepFinished):
            if event.transition_id is not None:
                transition_id = event.transition_id
                source = self.transitions[transition_id.source]
                transition = source[transition_id.status_code][event.target][transition_id.name]
                if event.response is not None:
                    key = event.response.status_code
                else:
                    key = None
                counter = transition.setdefault(key, 0)
                transition[key] = counter + 1
            else:
                # A start of a sequence has an empty source and does not belong to any transition
                target = self.roots.setdefault(event.target, {})
                if event.response is not None:
                    key = event.response.status_code
                else:
                    key = None
                counter = target.setdefault(key, 0)
                target[key] = counter + 1

    def copy(self) -> OpenAPILinkStats:
        return self.__class__(transitions=fast_deepcopy(self.transitions))

    def iter(self) -> Iterator[StatisticEntry]:
        for source_idx, (source, responses) in enumerate(self.transitions.items()):
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

    def to_formatted_table(self, width: int) -> str:
        """Format the statistic as a table."""
        entries = list(self.iter_with_format())
        lines: List[str | list[str]] = [HEADER, ""]
        column_widths = [len(column) for column in HEADER]
        for entry in entries:
            if isinstance(entry.entry, Link):
                aggregated = _aggregate_responses(entry.entry.responses)
                values = [
                    entry.line,
                    str(aggregated["2xx"]),
                    str(aggregated["4xx"]),
                    str(aggregated["5xx"]),
                    str(aggregated["Total"]),
                ]
                column_widths = [max(column_widths[idx], len(column)) for idx, column in enumerate(values)]
                lines.append(values)
            else:
                lines.append(entry.line)
        used_width = sum(column_widths) + 4 * PADDING
        max_space = width - used_width if used_width < width else 0
        formatted_lines = []

        for line in lines:
            if isinstance(line, list):
                formatted_line, *counters = line
                formatted_line = formatted_line.ljust(column_widths[0] + max_space)

                for column, max_width in zip(counters, column_widths[1:]):
                    formatted_line += f"{column:>{max_width + PADDING}}"

                formatted_lines.append(formatted_line)
            else:
                formatted_lines.append(line)

        return "\n".join(formatted_lines)


PADDING = 4
HEADER = ["Links", "2xx", "4xx", "5xx", "Total"]


def _aggregate_responses(responses: ResponseCounter) -> AggregatedResponseCounter:
    """Aggregate responses by status code ranges."""
    output: AggregatedResponseCounter = {
        "2xx": 0,
        # NOTE: 3xx responses are not counted
        "4xx": 0,
        "5xx": 0,
        "Total": 0,
    }
    for status_code, count in responses.items():
        if status_code is not None:
            if 200 <= status_code < 300:
                output["2xx"] += count
                output["Total"] += count
            elif 400 <= status_code < 500:
                output["4xx"] += count
                output["Total"] += count
            elif 500 <= status_code < 600:
                output["5xx"] += count
                output["Total"] += count
    return output
