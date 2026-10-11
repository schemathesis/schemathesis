from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from schemathesis.core.error_feedback import ErrorFeedbackStore
    from schemathesis.python._constants.pool import ConstantsPool
    from schemathesis.resources import ExtraDataSource


@dataclass(frozen=True, slots=True)
class FeedbackSources:
    """Runtime learning channels that steer generation for one operation.

    A typed carrier for the feedback signals the engine feeds into strategy
    building, replacing loose string keys in the `as_strategy` kwargs bag.
    """

    extra_data_source: ExtraDataSource | None = None
    error_feedback: ErrorFeedbackStore | None = None
    constants_value_source: ConstantsPool | None = None

    def for_examples(self) -> FeedbackSources:
        # The examples phase never draws from extracted constants.
        return replace(self, constants_value_source=None)


NO_FEEDBACK = FeedbackSources()
