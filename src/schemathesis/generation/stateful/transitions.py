"""Spec-agnostic transition graph contract used by `TransitionController`."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence


class _Endpoint(Protocol):
    @property
    def label(self) -> str: ...


class _Edge(Protocol):
    @property
    def source(self) -> _Endpoint: ...

    @property
    def target(self) -> _Endpoint: ...


class _OperationTransitions(Protocol):
    @property
    def incoming(self) -> Sequence[_Edge]: ...

    @property
    def outgoing(self) -> Sequence[_Edge]: ...


class Transitions(Protocol):
    """Concrete spec-specific transition structures must satisfy this shape."""

    @property
    def operations(self) -> Mapping[str, _OperationTransitions]: ...
