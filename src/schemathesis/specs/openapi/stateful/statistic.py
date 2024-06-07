from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Iterator, Union


from ....internal.copy import fast_deepcopy
from ....stateful.statistic import TransitionStats
from .types import LinkName, ResponseCounter, SourceName, StatusCode, TargetName

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
