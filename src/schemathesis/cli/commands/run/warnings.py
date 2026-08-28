from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass

from schemathesis.cli.commands.run.filters import describe_filter
from schemathesis.cli.context import BaseExecutionContext
from schemathesis.cli.summary import WarningData
from schemathesis.config import ProjectConfig, SchemathesisWarning
from schemathesis.core.errors import RefResolutionError
from schemathesis.core.parameters import ParameterLocation
from schemathesis.core.statistic import ApiStatistic
from schemathesis.engine import Status, events
from schemathesis.engine.recorder import Interaction, RecordedScenario
from schemathesis.engine.run import PhaseName
from schemathesis.generation.meta import CoveragePhaseData, CoverageScenario
from schemathesis.generation.modes import GenerationMode


@dataclass(slots=True)
class StatusCodeStatistic:
    """Statistics about HTTP status codes in a scenario."""

    counts: dict[int, int]
    total: int

    def ratio_for(self, status_code: int) -> float:
        """Calculate the ratio of responses with the given status code."""
        if self.total == 0:
            return 0.0
        return self.counts.get(status_code, 0) / self.total

    def _get_4xx_breakdown(self) -> tuple[int, int, int]:
        """Get breakdown of 4xx responses: (404_count, other_4xx_count, total_4xx_count)."""
        count_404 = self.counts.get(404, 0)
        count_other_4xx = sum(
            count for code, count in self.counts.items() if 400 <= code < 500 and code not in {401, 403, 404}
        )
        total_4xx = count_404 + count_other_4xx
        return count_404, count_other_4xx, total_4xx

    def _is_only_4xx_responses(self) -> bool:
        """Check if all responses are 4xx (excluding 5xx)."""
        return all(400 <= code < 500 for code in self.counts if code != 500)

    def _can_warn_about_4xx(self) -> bool:
        """Check basic conditions for 4xx warnings."""
        if self.total == 0:
            return False
        # Skip if only auth errors
        if set(self.counts.keys()) <= {401, 403, 500}:
            return False
        return self._is_only_4xx_responses()

    def should_warn_about_missing_test_data(self) -> bool:
        """Check if an operation should be warned about missing test data (significant 404 responses)."""
        if not self._can_warn_about_4xx():
            return False

        count_404, _, total_4xx = self._get_4xx_breakdown()

        if total_4xx == 0:
            return False

        return (count_404 / total_4xx) >= OTHER_CLIENT_ERRORS_THRESHOLD

    def should_warn_about_validation_mismatch(self) -> bool:
        """Check if an operation should be warned about validation mismatch (significant 400/422 responses)."""
        if not self._can_warn_about_4xx():
            return False

        _, count_other_4xx, total_4xx = self._get_4xx_breakdown()

        if total_4xx == 0:
            return False

        return (count_other_4xx / total_4xx) >= OTHER_CLIENT_ERRORS_THRESHOLD


AUTH_ERRORS_THRESHOLD = 0.9
OTHER_CLIENT_ERRORS_THRESHOLD = 0.1


def aggregate_status_codes(interactions: Iterable[Interaction]) -> StatusCodeStatistic:
    """Analyze status codes from interactions."""
    counts: dict[int, int] = {}
    total = 0

    for interaction in interactions:
        if interaction.response is not None:
            status = interaction.response.status_code
            counts[status] = counts.get(status, 0) + 1
            total += 1

    return StatusCodeStatistic(counts=counts, total=total)


def auth_error_codes(statistic: StatusCodeStatistic, recorder: RecordedScenario) -> tuple[int, ...]:
    """Auth status codes that dominate the scenario."""
    cases = list(recorder.cases.values())
    if len(cases) == 1:
        meta = cases[0].value.meta
        # A scenario that only omits `Authorization` is expected to be rejected.
        if (
            meta is not None
            and isinstance(meta.phase.data, CoveragePhaseData)
            and meta.phase.data.scenario == CoverageScenario.MISSING_PARAMETER
            and meta.phase.data.parameter == "Authorization"
            and meta.phase.data.parameter_location == ParameterLocation.HEADER
        ):
            return ()
    return tuple(code for code in (401, 403) if statistic.ratio_for(code) >= AUTH_ERRORS_THRESHOLD)


def all_positive_are_rejected(recorder: RecordedScenario) -> bool:
    """Whether the scenario generated positive test cases and none of them got a 2xx response."""
    seen_positive = False
    for case in recorder.cases.values():
        if not (case.value.meta is not None and case.value.meta.generation.mode == GenerationMode.POSITIVE):
            continue
        seen_positive = True
        interaction = recorder.interactions.get(case.value.id)
        if not (interaction is not None and interaction.response is not None):
            continue
        # At least one positive response for positive test case
        if 200 <= interaction.response.status_code < 300:
            return False
    # If there are positive test cases, and we ended up here, then there are no 2xx responses for them
    # Otherwise, there are no positive test cases at all and this check should pass
    return seen_positive


class WarningCollector:
    config: ProjectConfig
    data: WarningData

    def __init__(self, config: ProjectConfig) -> None:
        self.config = config
        self.data = WarningData()

    def on_scenario_finished(self, ctx: BaseExecutionContext, event: events.ScenarioFinished) -> None:
        if event.phase in (PhaseName.EXAMPLES, PhaseName.COVERAGE, PhaseName.FUZZING):
            self._check_warnings(ctx, event)
        elif (
            event.phase == PhaseName.STATEFUL_TESTING
            and not event.is_final
            and event.status not in (Status.INTERRUPTED, Status.SKIP, None)
        ):
            self._check_stateful_warnings(ctx, event)

    def on_schema_warnings(self, ctx: BaseExecutionContext, event: events.SchemaAnalysisWarnings) -> None:
        """Process schema-level warnings emitted outside of scenarios."""
        for warning in event.warnings:
            if warning.kind is SchemathesisWarning.MISSING_DESERIALIZER:
                # MissingDeserializerWarning always has operation_label and a media type group
                assert warning.operation_label is not None
                assert warning.group is not None
                self._handle_warning(
                    ctx,
                    warning.kind,
                    self._record_missing_deserializer_warning(warning.operation_label, warning.group, warning.message),
                )
            elif warning.kind is SchemathesisWarning.UNUSED_OPENAPI_AUTH:
                # UnusedOpenAPIAuthWarning has no operation_label (schema-level)
                self._handle_warning(
                    ctx,
                    warning.kind,
                    self._record_unused_openapi_auth_warning(warning.message),
                )
            elif warning.kind is SchemathesisWarning.UNSUPPORTED_REGEX:
                assert warning.operation_label is not None
                self._handle_warning(
                    ctx,
                    warning.kind,
                    self._record_unsupported_regex_warning(warning.operation_label, warning.message),
                )
            elif warning.kind is SchemathesisWarning.CONSTANTS_EXTRACTION:
                self._handle_warning(
                    ctx,
                    warning.kind,
                    self._record_constants_extraction_warning(warning.message),
                )

    def _check_warnings(self, ctx: BaseExecutionContext, event: events.ScenarioFinished) -> None:
        if event.skip_warning is not None and event.label is not None:
            self._record_skip_warning(ctx, event)
            # Synthetic skip scenarios carry no interactions to inspect.
            return

        statistic = aggregate_status_codes(event.recorder.interactions.values())

        if statistic.total == 0:
            return

        assert ctx.find_operation_by_label is not None
        assert event.label is not None
        try:
            operation = ctx.find_operation_by_label(event.label)
        except RefResolutionError:
            # This error will be reported elsewhere anyway
            return None

        warnings = self.config.warnings_for(operation=operation)

        if warnings.should_display(SchemathesisWarning.MISSING_AUTH):
            for status_code in auth_error_codes(statistic, event.recorder):
                self.data.missing_auth.setdefault(status_code, set()).add(event.recorder.label)
                # Check if this warning should cause test failure
                if warnings.should_fail(SchemathesisWarning.MISSING_AUTH):
                    ctx.exit_code = 1

        # Warn if all positive test cases got 4xx in return and no failure was found
        if (
            event.status == Status.SUCCESS
            and (
                warnings.should_display(SchemathesisWarning.MISSING_TEST_DATA)
                or warnings.should_display(SchemathesisWarning.VALIDATION_MISMATCH)
            )
            and GenerationMode.POSITIVE
            in self.config.generation_for(operation=operation, phase=event.phase.value).modes
            and all_positive_are_rejected(event.recorder)
        ):
            if statistic.should_warn_about_missing_test_data():
                self._handle_warning(
                    ctx,
                    SchemathesisWarning.MISSING_TEST_DATA,
                    lambda: self.data.missing_test_data.add(event.recorder.label),
                )
            if statistic.should_warn_about_validation_mismatch():
                self._handle_warning(
                    ctx,
                    SchemathesisWarning.VALIDATION_MISMATCH,
                    lambda: self.data.validation_mismatch.add(event.recorder.label),
                )

    def _handle_warning(
        self, ctx: BaseExecutionContext, kind: SchemathesisWarning, record_callback: Callable[[], None]
    ) -> None:
        """Handle a warning by checking display/fail config and recording it."""
        if not self.config.warnings.should_display(kind):
            return
        record_callback()
        if self.config.warnings.should_fail(kind):
            ctx.exit_code = 1

    def _record_skip_warning(self, ctx: BaseExecutionContext, event: events.ScenarioFinished) -> None:
        """Record a warning surfaced via a supervisor-driven scenario skip."""
        assert event.skip_warning is not None
        assert event.label is not None
        assert ctx.find_operation_by_label is not None
        operation = ctx.find_operation_by_label(event.label)
        warnings = self.config.warnings_for(operation=operation)
        if event.skip_warning is SchemathesisWarning.METHOD_NOT_ALLOWED and warnings.should_display(
            SchemathesisWarning.METHOD_NOT_ALLOWED
        ):
            self.data.method_not_allowed.add(event.label)
            if warnings.should_fail(SchemathesisWarning.METHOD_NOT_ALLOWED):
                ctx.exit_code = 1

    def _record_missing_deserializer_warning(
        self, operation_label: str, media_type: str, status_code: str
    ) -> Callable[[], None]:
        """Create a callback that records a missing deserializer warning."""

        def record() -> None:
            self.data.missing_deserializer.setdefault(media_type, {}).setdefault(operation_label, set()).add(
                status_code
            )

        return record

    def _record_unused_openapi_auth_warning(self, message: str) -> Callable[[], None]:
        """Create a callback that records an unused OpenAPI auth warning."""

        def record() -> None:
            self.data.unused_openapi_auth.add(message)

        return record

    def _record_unsupported_regex_warning(self, operation_label: str, message: str) -> Callable[[], None]:
        """Create a callback that records an unsupported regex warning."""

        def record() -> None:
            self.data.unsupported_regex.setdefault(operation_label, set()).add(message)

        return record

    def on_unmatched_filters(self, ctx: BaseExecutionContext, statistic: ApiStatistic) -> None:
        """Record filter expressions that no operation matched."""
        for entry in statistic.unmatched_filters:
            message = describe_filter(entry)
            if entry.suggestion is not None:
                message += f" (did you mean {entry.suggestion!r}?)"
            self._handle_warning(ctx, SchemathesisWarning.UNMATCHED_FILTER, self._record_unmatched_filter(message))

    def _record_unmatched_filter(self, message: str) -> Callable[[], None]:
        def record() -> None:
            self.data.unmatched_filter.add(message)

        return record

    def _record_constants_extraction_warning(self, message: str) -> Callable[[], None]:
        """Create a callback that records a constants extraction warning."""

        def record() -> None:
            self.data.constants_extraction.add(message)

        return record

    def _check_stateful_warnings(self, ctx: BaseExecutionContext, event: events.ScenarioFinished) -> None:
        # If stateful testing had successful responses for API operations that were marked with "missing_test_data"
        # warnings, then remove them from warnings
        for key, node in event.recorder.cases.items():
            if not self.data.missing_test_data:
                break
            if node.value.operation.label in self.data.missing_test_data and key in event.recorder.interactions:
                response = event.recorder.interactions[key].response
                if response is not None and response.status_code < 300:
                    self.data.missing_test_data.remove(node.value.operation.label)
                    continue
