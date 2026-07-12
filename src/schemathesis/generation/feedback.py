from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from schemathesis.core.error_feedback import ErrorFeedbackStore
    from schemathesis.resources import ExtraDataSource


@dataclass(frozen=True, slots=True)
class FeedbackSources:
    """Runtime learning channels that steer generation for one operation.

    A typed carrier for the feedback signals the engine feeds into strategy
    building, replacing loose string keys in the `as_strategy` kwargs bag.
    """

    extra_data_source: ExtraDataSource | None = None
    error_feedback: ErrorFeedbackStore | None = None
