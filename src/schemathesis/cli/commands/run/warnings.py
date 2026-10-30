from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from schemathesis.cli.commands.run.filters import describe_filter
from schemathesis.config import ProjectConfig, SchemathesisWarning, WarningsConfig
from schemathesis.core import SpecificationFeature
from schemathesis.core.errors import RefResolutionError
from schemathesis.core.parameters import ParameterLocation
from schemathesis.core.statistic import ApiStatistic
from schemathesis.core.transport import CallOutcome
from schemathesis.engine import Status, events
from schemathesis.engine.recorder import CaseNode, RecordedScenario
from schemathesis.engine.run import UNIT_PHASES, PhaseName
from schemathesis.engine.summary import ValidRate, WarningData
from schemathesis.generation.meta import REQUEST_SHAPE_PROBES, CoveragePhaseData, CoverageScenario, coverage_scenario
from schemathesis.generation.modes import GenerationMode

if TYPE_CHECKING:
    from schemathesis.cli.context import BaseExecutionContext
    from schemathesis.schemas import APIOperation, BaseSchema


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
        """Get breakdown of 4xx responses: (404_count, other_4xx_count, total_4xx_count).

        Auth, conflict and rate-limit responses say nothing about the data sent, so they are left out.
        """
        count_404 = self.counts.get(404, 0)
        count_other_4xx = sum(
            count for code, count in self.counts.items() if 400 <= code < 500 and code not in {401, 403, 404, 409, 429}
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
RATE_LIMITED_THRESHOLD = 0.9
OTHER_CLIENT_ERRORS_THRESHOLD = 0.1
# Fewer calls than this and the share accepted is noise rather than a rate.
MIN_CALLS_FOR_VALID_RATE = 10


def positive_call_outcomes(recorder: RecordedScenario) -> ValidRate:
    """Classify every positive case by what the API did with it.

    Auth rejections and server errors say nothing about whether the data was acceptable, so they
    stay out of the count entirely rather than counting against the rate.
    """
    outcomes = ValidRate()
    for case in recorder.cases.values():
        if not _is_positive(case):
            continue
        interaction = recorder.interactions.get(case.value.id)
        if interaction is None or interaction.response is None:
            continue
        outcome = case.value.operation.schema.classify_call_outcome(case.value, interaction.response)
        if outcome is CallOutcome.ACCEPTED:
            outcomes.accepted += 1
        elif outcome is CallOutcome.UNREACHABLE:
            outcomes.unreachable += 1
        elif outcome is CallOutcome.CONFLICT:
            outcomes.conflicts += 1
        elif outcome is CallOutcome.REJECTED:
            outcomes.rejected += 1
    return outcomes


def aggregate_status_codes(recorder: RecordedScenario, *, positive_only: bool = False) -> StatusCodeStatistic:
    """Analyze status codes the operation answered with.

    Requests with a method the operation does not declare reach a different operation, or none,
    so their responses say nothing about this one. With `positive_only`, responses to cases meant
    to be rejected are left out too.
    """
    counts: dict[int, int] = {}
    total = 0

    for case_id, interaction in recorder.interactions.items():
        case = recorder.cases.get(case_id)
        if case is not None and _is_request_shape_probe(case):
            continue
        if positive_only and (case is None or not _is_positive(case)):
            continue
        if interaction.response is not None:
            status = interaction.response.status_code
            # A client error that says nothing about the data, e.g. a cancelled gRPC call, is no rejection of it.
            if (
                positive_only
                and case is not None
                and 400 <= status < 500
                and case.value.operation.schema.classify_call_outcome(case.value, interaction.response)
                is CallOutcome.UNINFORMATIVE
            ):
                continue
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


def missing_base_path(operation: APIOperation) -> str | None:
    """The path the schema declares that the configured base URL leaves out, if any.

    `--url` is the complete base URL by design, so nothing is merged from the schema. When every
    request 404s, the difference between the two is the likely cause.
    """
    schema = operation.schema
    configured = schema.config.base_url
    if configured is None:
        return None
    declared = schema.declared_base_path().rstrip("/")
    if not declared:
        return None
    return declared if not urlsplit(configured).path.rstrip("/").endswith(declared) else None


def takes_input(operation: APIOperation) -> bool:
    """Whether the operation has anything a run could have got wrong."""
    return bool(
        operation.path_parameters or operation.query or operation.headers or operation.cookies or operation.body
    )


def auth_flow_suggestion(schema: BaseSchema) -> str | None:
    """Config that logs in through the sign-up and login operations the schema declares, if any."""
    flow = schema.auth_flow()
    if flow is None or flow.is_supplied():
        return None
    snippet = "\n".join(f"    {line}" for line in flow.config_lines())
    return (
        f"{flow.spec.register_operation} and {flow.spec.login_operation} look like a sign-up and login flow. "
        f"Register an account, then add to schemathesis.toml:\n\n{snippet}"
    )


def _is_request_shape_probe(case: CaseNode) -> bool:
    return coverage_scenario(case.value) in REQUEST_SHAPE_PROBES


def _is_positive(case: CaseNode) -> bool:
    return case.value.meta is not None and case.value.meta.generation.mode == GenerationMode.POSITIVE


def any_positive_is_accepted(recorder: RecordedScenario) -> bool:
    """Whether the API served any of the positive test cases.

    Negative cases are excluded: an undocumented method may well be served while the operation
    itself is refused.
    """
    for case in recorder.cases.values():
        if not _is_positive(case):
            continue
        interaction = recorder.interactions.get(case.value.id)
        if interaction is None or interaction.response is None:
            continue
        if case.value.operation.schema.classify_call_outcome(case.value, interaction.response) is CallOutcome.ACCEPTED:
            return True
    return False


def none_served(recorder: RecordedScenario) -> bool:
    """Whether the server answered every request saying it does not serve the operation."""
    for case_id, interaction in recorder.interactions.items():
        response = interaction.response
        case = recorder.cases[case_id].value
        if (
            response is None
            or case.operation.schema.classify_call_outcome(case, response) is not CallOutcome.NOT_SERVED
        ):
            return False
    return True


def all_positive_are_rejected(recorder: RecordedScenario) -> bool:
    """Whether the scenario generated positive test cases and the API served none of them."""
    return any(_is_positive(case) for case in recorder.cases.values()) and not any_positive_is_accepted(recorder)


class WarningCollector:
    config: ProjectConfig
    data: WarningData
    # Operations that got past authentication at some point in the run.
    authenticated: set[str]
    # Operations that served positive data at some point in the run.
    served: set[str]
    # Whether a warning configured with `fail-on` was recorded that nothing withdraws later.
    fails: bool
    # Operations whose missing-auth warning is configured with `fail-on`; a later success withdraws them.
    fails_on_auth: set[str]
    # Operations whose missing-test-data warning is configured with `fail-on`; a later success withdraws them.
    fails_on_missing_test_data: set[str]

    def __init__(self, config: ProjectConfig) -> None:
        self.config = config
        self.data = WarningData()
        self.authenticated = set()
        self.served = set()
        self.fails = False
        self.fails_on_auth = set()
        self.fails_on_missing_test_data = set()

    def fails_the_run(self) -> bool:
        """Whether a warning configured with `fail-on` still stands."""
        return self.fails or bool(self.fails_on_auth) or bool(self.fails_on_missing_test_data)

    def on_startup_warning(self, kind: SchemathesisWarning) -> None:
        if self.config.warnings.should_fail(kind):
            self.fails = True

    def on_scenario_finished(self, ctx: BaseExecutionContext, event: events.ScenarioFinished) -> None:
        if event.phase in UNIT_PHASES:
            self._check_warnings(ctx, event)
        elif (
            event.phase == PhaseName.STATEFUL_TESTING
            and not event.is_final
            and event.status not in (Status.INTERRUPTED, Status.SKIP, None)
        ):
            self._check_stateful_warnings(ctx, event)

    def on_schema_warnings(self, event: events.SchemaAnalysisWarnings) -> None:
        """Process schema-level warnings emitted outside of scenarios."""
        for warning in event.warnings:
            if warning.kind is SchemathesisWarning.MISSING_DESERIALIZER:
                # MissingDeserializerWarning always has operation_label and a media type group
                assert warning.operation_label is not None
                assert warning.group is not None
                self._handle_warning(
                    warning.kind,
                    self._record_missing_deserializer_warning(warning.operation_label, warning.group, warning.message),
                )
            elif warning.kind is SchemathesisWarning.UNUSED_OPENAPI_AUTH:
                # UnusedOpenAPIAuthWarning has no operation_label (schema-level)
                self._handle_warning(
                    warning.kind,
                    self._record_unused_openapi_auth_warning(warning.message),
                )
            elif warning.kind is SchemathesisWarning.UNSUPPORTED_REGEX:
                assert warning.operation_label is not None
                self._handle_warning(
                    warning.kind,
                    self._record_unsupported_regex_warning(warning.operation_label, warning.message),
                )
            elif warning.kind is SchemathesisWarning.UNRESOLVABLE_REFERENCE:
                assert warning.operation_label is not None
                self._handle_warning(
                    warning.kind,
                    self._record_unresolvable_reference_warning(warning.operation_label, warning.message),
                )
            elif warning.kind is SchemathesisWarning.UNSUPPORTED_FIELD:
                assert warning.operation_label is not None
                self._handle_warning(
                    warning.kind,
                    self._record_unsupported_field_warning(warning.operation_label, warning.message),
                )
            elif warning.kind is SchemathesisWarning.DICTIONARY_MISMATCH:
                self._handle_warning(
                    warning.kind,
                    self._record_dictionary_mismatch_warning(warning.message),
                )
            elif warning.kind is SchemathesisWarning.CONSTANTS_EXTRACTION:
                self._handle_warning(
                    warning.kind,
                    self._record_constants_extraction_warning(warning.message),
                )

    def _check_warnings(self, ctx: BaseExecutionContext, event: events.ScenarioFinished) -> None:
        if event.skip_warning is not None and event.label is not None:
            self._record_skip_warning(ctx, event)
            # Synthetic skip scenarios carry no interactions to inspect.
            return

        statistic = aggregate_status_codes(event.recorder)

        if statistic.total == 0:
            return

        # Warnings that explain why valid input was rejected look only at valid input.
        positive = aggregate_status_codes(event.recorder, positive_only=True)

        assert event.label is not None
        try:
            operation = ctx.find_operation(event.label)
        except RefResolutionError:
            # This error will be reported elsewhere anyway
            return None

        warnings = self.config.warnings_for(operation=operation)

        if any_positive_is_accepted(event.recorder):
            self._mark_authenticated(event.recorder.label)
            self._mark_served(event.recorder.label)

        if warnings.should_display(SchemathesisWarning.MISSING_AUTH) and event.recorder.label not in self.authenticated:
            for status_code in auth_error_codes(statistic, event.recorder):
                self.data.missing_auth.setdefault(status_code, set()).add(event.recorder.label)
                if self.data.auth_flow_suggestion is None and operation is not None:
                    self.data.auth_flow_suggestion = auth_flow_suggestion(operation.schema)
                # Check if this warning should cause test failure
                if warnings.should_fail(SchemathesisWarning.MISSING_AUTH):
                    self.fails_on_auth.add(event.recorder.label)

        if statistic.ratio_for(429) >= RATE_LIMITED_THRESHOLD and warnings.should_display(
            SchemathesisWarning.RATE_LIMITED
        ):
            self._handle_warning(
                SchemathesisWarning.RATE_LIMITED,
                lambda: self.data.rate_limited.add(event.recorder.label),
                warnings,
            )

        if warnings.should_display(SchemathesisWarning.NOT_SERVED) and none_served(event.recorder):
            self._handle_warning(
                SchemathesisWarning.NOT_SERVED, lambda: self.data.not_served.add(event.recorder.label), warnings
            )

        # A wrong base URL 404s everything, and those 404s trip other checks - so this must not be
        # gated on the scenario passing, unlike the generic 404 warning below.
        if (
            warnings.should_display(SchemathesisWarning.BASE_URL_MISMATCH)
            and GenerationMode.POSITIVE
            in self.config.generation_for(operation=operation, phase=event.phase.value).modes
            and all_positive_are_rejected(event.recorder)
            and positive.should_warn_about_missing_test_data()
        ):
            missing = missing_base_path(operation) if operation is not None else None
            if missing is not None:
                assert operation is not None
                self.data.base_url_suggestion = f"{(operation.schema.config.base_url or '').rstrip('/')}{missing}"
                self._handle_warning(
                    SchemathesisWarning.BASE_URL_MISMATCH,
                    lambda: self.data.base_url_mismatch.add(event.recorder.label),
                    warnings,
                )

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
            if positive.should_warn_about_missing_test_data():
                self._handle_warning(
                    SchemathesisWarning.MISSING_TEST_DATA,
                    lambda: self._record_missing_test_data(event.recorder.label, operation),
                    warnings,
                )
            if positive.should_warn_about_validation_mismatch():
                self._handle_warning(
                    SchemathesisWarning.VALIDATION_MISMATCH,
                    lambda: self.data.validation_mismatch.add(event.recorder.label),
                    warnings,
                )
            # Where the status is the same whether the request was served or refused, the two checks above
            # stay silent; the response bodies are the only evidence either way.
            if operation is not None and not operation.schema.specification.supports_feature(
                SpecificationFeature.STATUS_CODE_ANALYSIS
            ):
                self._handle_warning(
                    SchemathesisWarning.MISSING_TEST_DATA,
                    lambda: self._record_missing_test_data(event.recorder.label, operation),
                    warnings,
                )

        # A run that did not ask for positive data has no valid-input rate worth reporting; the few
        # positive cases other phases contribute are incidental.
        if (
            GenerationMode.POSITIVE
            not in self.config.generation_for(operation=operation, phase=event.phase.value).modes
        ):
            return

        outcomes = positive_call_outcomes(event.recorder)
        if outcomes.total:
            self.data.valid_rates.setdefault(event.recorder.label, {})[event.phase.value] = outcomes
            # An operation nothing reached at all belongs to the two warnings above, which say more
            # about why. This one is for the middle ground they leave silent.
            if (
                outcomes.accepted
                and outcomes.total >= MIN_CALLS_FOR_VALID_RATE
                and outcomes.rate < warnings.low_valid_rate.threshold
            ):
                self._handle_warning(
                    SchemathesisWarning.LOW_VALID_RATE,
                    lambda: self.data.low_valid_rate.add(event.recorder.label),
                    warnings,
                )

    def _record_missing_test_data(self, label: str, operation: APIOperation | None) -> None:
        """Record the operation, capturing the link graph the first time one warns."""
        if label in self.served:
            return
        if operation is not None:
            if self.data.linked_operations is None:
                self.data.linked_operations = operation.schema.operations_with_incoming_links()
            if self.data.resource_producers is None:
                self.data.resource_producers = operation.schema.resource_producers()
            if not takes_input(operation):
                self.data.parameterless.add(label)
        self.data.missing_test_data.add(label)

    def _mark_authenticated(self, label: str) -> None:
        """Take an operation out of the auth warning, and keep it out for the rest of the run."""
        self.authenticated.add(label)
        self.fails_on_auth.discard(label)
        for status_code, labels in list(self.data.missing_auth.items()):
            labels.discard(label)
            if not labels:
                del self.data.missing_auth[status_code]

    def _mark_served(self, label: str) -> None:
        """Take an operation out of the missing-test-data warning, and keep it out for the rest of the run."""
        self.served.add(label)
        self.data.missing_test_data.discard(label)
        self.fails_on_missing_test_data.discard(label)

    def _handle_warning(
        self, kind: SchemathesisWarning, record_callback: Callable[[], None], warnings: WarningsConfig | None = None
    ) -> None:
        """Handle a warning by checking display/fail config and recording it."""
        if warnings is None:
            warnings = self.config.warnings
        if not warnings.should_display(kind):
            return
        already_missing = set(self.data.missing_test_data)
        record_callback()
        if warnings.should_fail(kind):
            if kind is SchemathesisWarning.MISSING_TEST_DATA:
                # Only operations recorded under this config fail the run; others may not set `fail-on`.
                self.fails_on_missing_test_data.update(self.data.missing_test_data - already_missing)
            else:
                self.fails = True

    def _record_skip_warning(self, ctx: BaseExecutionContext, event: events.ScenarioFinished) -> None:
        """Record a warning surfaced via a supervisor-driven scenario skip."""
        assert event.skip_warning is not None
        assert event.label is not None
        operation = ctx.find_operation(event.label)
        warnings = self.config.warnings_for(operation=operation)
        if event.skip_warning is SchemathesisWarning.METHOD_NOT_ALLOWED and warnings.should_display(
            SchemathesisWarning.METHOD_NOT_ALLOWED
        ):
            self.data.method_not_allowed.add(event.label)
            if warnings.should_fail(SchemathesisWarning.METHOD_NOT_ALLOWED):
                self.fails = True

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

    def _record_unresolvable_reference_warning(self, operation_label: str, message: str) -> Callable[[], None]:
        """Create a callback that records a dropped parameter with an unresolvable reference."""

        def record() -> None:
            self.data.unresolvable_reference.setdefault(operation_label, set()).add(message)

        return record

    def _record_unsupported_field_warning(self, operation_label: str, message: str) -> Callable[[], None]:
        """Create a callback that records a request field Schemathesis does not generate."""

        def record() -> None:
            self.data.unsupported_field.setdefault(operation_label, set()).add(message)

        return record

    def on_unmatched_filters(self, statistic: ApiStatistic) -> None:
        """Record filter expressions that no operation matched."""
        for entry in statistic.unmatched_filters:
            message = describe_filter(entry)
            if entry.suggestion is not None:
                message += f" (did you mean {entry.suggestion!r}?)"
            self._handle_warning(SchemathesisWarning.UNMATCHED_FILTER, self._record_unmatched_filter(message))

    def _record_unmatched_filter(self, message: str) -> Callable[[], None]:
        def record() -> None:
            self.data.unmatched_filter.add(message)

        return record

    def _record_dictionary_mismatch_warning(self, message: str) -> Callable[[], None]:
        def record() -> None:
            self.data.dictionary_mismatch.add(message)

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
            label = node.value.operation.label
            self.data.stateful_exercised.add(label)
            if label in self.data.missing_test_data and key in event.recorder.interactions:
                response = event.recorder.interactions[key].response
                if response is None:
                    continue
                if node.value.operation.schema.classify_call_outcome(node.value, response) is CallOutcome.ACCEPTED:
                    self._mark_served(label)
