"""Replay cached requests during probing and hydrate runtime stores; advisory (any failure no-ops)."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum, auto
from typing import TYPE_CHECKING, cast

import requests

from schemathesis.core import NOT_SET
from schemathesis.core.cache import (
    FORMAT_VERSION,
    MANIFEST_FILENAME,
    CacheWriter,
    Entry,
    Kind,
    Manifest,
    Request,
    effective_directory,
    load,
    sanitize_request,
    write,
)
from schemathesis.core.error_feedback import ObservationKind
from schemathesis.core.error_feedback.collector import parse_observations
from schemathesis.core.parameters import ParameterLocation
from schemathesis.core.version import SCHEMATHESIS_VERSION
from schemathesis.engine.recorder import ScenarioRecorder

if TYPE_CHECKING:
    from schemathesis.core.transport import HttpMethod, Response
    from schemathesis.engine.context import EngineContext
    from schemathesis.generation.case import Case
    from schemathesis.schemas import APIOperation

_BUDGET = 100
_MAX_ENTRIES_PER_OPERATION = 100


@dataclass(slots=True)
class CacheReport:
    """One cache replay pass result; `available=False` means corrupt-but-present file."""

    replayed: int = 0
    dropped: int = 0
    skipped: int = 0
    available: bool = True


class _Outcome(Enum):
    CONFIRMED = auto()
    CONTRADICTED = auto()
    SKIPPED = auto()


class Cache:
    """Engine-scoped cache controller: discovery buffer + replay/persistence bound to one run."""

    __slots__ = ("_ctx", "writer")

    def __init__(self, ctx: EngineContext) -> None:
        self._ctx = ctx
        self.writer = CacheWriter()

    def record(
        self,
        kind: Kind,
        operation: str,
        request: Request,
        observation_keys: Iterable[str] = (),
    ) -> None:
        """Capture a live discovery for end-of-run persistence."""
        self.writer.record(kind, operation, request, observation_keys=observation_keys)

    def run(self) -> CacheReport | None:
        """Replay cached entries during probing and hydrate runtime stores."""
        return _run(self._ctx)

    def flush(self) -> None:
        """Persist newly discovered entries from this run to disk."""
        _flush(self._ctx, self.writer)


def _run(ctx: EngineContext) -> CacheReport | None:
    """Replay cached requests, hydrate runtime stores; `None` if disabled or no cache file."""
    cache_config = ctx.config.cache
    if not cache_config.enabled:
        return None

    directory = effective_directory(cache_config.directory, _active_project_title(ctx))
    manifest_path = directory / MANIFEST_FILENAME
    try:
        manifest_present = manifest_path.is_file()
    except OSError:
        # Permission denied or other filesystem error -- surface as unavailable rather than crashing.
        return CacheReport(available=False)
    if not manifest_present:
        # No cache yet — nothing to render and nothing to do.
        return None
    loaded = load(directory)
    if loaded is None:
        # Cache file is present but unreadable — surface this so the user notices.
        return CacheReport(available=False)
    manifest, entries = loaded

    # Sort entries so the least-recently-replayed come first; newly-written
    # entries (`last_replayed_run == 0`) are picked up before anything that
    # has been validated in a previous run. Ties broken by id for stability.
    entries.sort(key=lambda entry: (entry.last_replayed_run, entry.id))
    current_run_id = manifest.next_run_id
    manifest.next_run_id += 1

    report = CacheReport()
    survivors: list[Entry] = []
    for entry in entries[:_BUDGET]:
        outcome = _verify(ctx, entry)
        if outcome is _Outcome.CONFIRMED:
            entry.last_replayed_run = current_run_id
            survivors.append(entry)
            report.replayed += 1
        elif outcome is _Outcome.SKIPPED:
            entry.last_replayed_run = current_run_id
            survivors.append(entry)
            report.skipped += 1
        else:
            report.dropped += 1
    # Entries past the budget keep their old `last_replayed_run`; next run's
    # sort puts them ahead of the entries we just validated, rotating coverage
    # across the whole file.
    survivors.extend(entries[_BUDGET:])

    if ctx.error_feedback is not None and report.replayed:
        ctx.error_feedback.checkpoint()

    try:
        write(directory, manifest, _sanitize_entries(survivors, ctx))
    except OSError:
        # Advisory cache -- disk errors must not fail the run.
        pass

    return report


def _flush(ctx: EngineContext, writer: CacheWriter) -> None:
    """Merge newly discovered entries into the on-disk cache; errors swallowed (advisory)."""
    if not ctx.config.cache.enabled:
        return
    if not writer.has_pending:
        return

    directory = effective_directory(ctx.config.cache.directory, _active_project_title(ctx))

    loaded = load(directory)
    if loaded is None:
        manifest = Manifest(
            format_version=FORMAT_VERSION,
            schemathesis_version=SCHEMATHESIS_VERSION,
            schema_location=ctx.schema.location or "",
            base_url=ctx.config.base_url or "",
            created_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        )
        entries: list[Entry] = []
    else:
        manifest, entries = loaded

    # Track which observation keys each (kind, operation) bucket already covers, plus which
    # (kind, operation) buckets already have the singleton entry (for auth_required / 405).
    claimed_observations: dict[tuple[Kind, str], set[str]] = {}
    claimed_singletons: set[tuple[Kind, str]] = set()
    for entry in entries:
        bucket = (entry.kind, entry.operation)
        if entry.observation_keys:
            claimed_observations.setdefault(bucket, set()).update(entry.observation_keys)
        else:
            claimed_singletons.add(bucket)

    next_id = max((entry.id for entry in entries), default=0) + 1
    for pending in writer.drain():
        bucket = (pending.kind, pending.operation)
        if pending.observation_keys:
            covered = claimed_observations.setdefault(bucket, set())
            new_keys = [key for key in pending.observation_keys if key not in covered]
            if not new_keys:
                continue
            covered.update(new_keys)
        else:
            if bucket in claimed_singletons:
                continue
            claimed_singletons.add(bucket)
            new_keys = []
        entries.append(
            Entry(
                id=next_id,
                kind=pending.kind,
                operation=pending.operation,
                request=pending.request,
                observation_keys=new_keys,
            )
        )
        next_id += 1

    entries = _enforce_per_operation_cap(entries)

    try:
        write(directory, manifest, _sanitize_entries(entries, ctx))
    except OSError:
        pass


def _sanitize_entries(entries: list[Entry], ctx: EngineContext) -> list[Entry]:
    """Strip configured sensitive fields from each entry's request before persistence."""
    return [
        Entry(
            id=entry.id,
            kind=entry.kind,
            operation=entry.operation,
            request=sanitize_request(entry.request, ctx.config.output.sanitization),
            observation_keys=list(entry.observation_keys),
            last_replayed_run=entry.last_replayed_run,
        )
        for entry in entries
    ]


def _enforce_per_operation_cap(entries: list[Entry]) -> list[Entry]:
    """Drop the oldest entries per `(kind, operation)` bucket beyond `_MAX_ENTRIES_PER_OPERATION`."""
    by_bucket: dict[tuple[Kind, str], list[Entry]] = {}
    for entry in entries:
        by_bucket.setdefault((entry.kind, entry.operation), []).append(entry)
    kept: list[Entry] = []
    for bucket_entries in by_bucket.values():
        if len(bucket_entries) <= _MAX_ENTRIES_PER_OPERATION:
            kept.extend(bucket_entries)
            continue
        bucket_entries.sort(key=lambda entry: entry.id, reverse=True)
        kept.extend(bucket_entries[:_MAX_ENTRIES_PER_OPERATION])
    kept.sort(key=lambda entry: entry.id)
    return kept


def _verify(ctx: EngineContext, entry: Entry) -> _Outcome:
    operation = ctx.schema.find_operation_by_label(entry.operation)
    if operation is None:
        return _Outcome.CONTRADICTED
    if entry.kind is Kind.ERROR_FEEDBACK:
        return _verify_error_feedback(ctx, entry, operation)
    if entry.kind is Kind.AUTH_REQUIRED:
        return _verify_auth_required(ctx, entry, operation)
    return _verify_method_not_allowed(ctx, entry, operation)


def _verify_error_feedback(ctx: EngineContext, entry: Entry, operation: APIOperation) -> _Outcome:
    if ctx.error_feedback is None:
        return _Outcome.SKIPPED
    result = _replay(ctx, entry, operation)
    if result is None:
        return _Outcome.SKIPPED
    case, response = result
    observations = parse_observations(operation=operation, case=case, response=response)
    if not observations:
        return _Outcome.CONTRADICTED
    for observation in observations:
        ctx.error_feedback.record(observation)
    return _Outcome.CONFIRMED


def _verify_auth_required(ctx: EngineContext, entry: Entry, operation: APIOperation) -> _Outcome:
    if ctx.error_feedback is None:
        return _Outcome.SKIPPED
    case = _build_case(operation, entry.request)
    unauth_response = _send_without_auth(ctx, case, operation)
    if unauth_response is None:
        return _Outcome.SKIPPED
    if unauth_response.status_code not in (401, 403):
        return _Outcome.CONTRADICTED

    recorder = ScenarioRecorder(label="cache-auth-replay")
    recorder.record_case(parent_id=None, case=case, transition=None, is_transition_applied=False)
    recorder.record_response(case_id=case.id, response=unauth_response)

    operation.schema.record_runtime_observations(
        store=ctx.error_feedback,
        recorder=recorder,
        case=case,
        response=unauth_response,
        transport_kwargs=ctx.get_transport_kwargs(operation=operation),
    )

    observations = ctx.error_feedback.observations(
        operation_label=operation.label,
        location=ParameterLocation.PATH,
    )
    if any(obs.kind is ObservationKind.REQUIRES_AUTHENTICATION for obs in observations):
        return _Outcome.CONFIRMED
    # 401/403 still present but no configured scheme recovered. Keep the entry; the user may fix credentials later.
    return _Outcome.SKIPPED


def _verify_method_not_allowed(ctx: EngineContext, entry: Entry, operation: APIOperation) -> _Outcome:
    result = _replay(ctx, entry, operation)
    if result is None:
        return _Outcome.SKIPPED
    _, response = result
    if response.status_code != 405:
        return _Outcome.CONTRADICTED
    ctx.supervisor.hydrate_method_not_allowed_skip(operation.label)
    return _Outcome.CONFIRMED


def _build_case(operation: APIOperation, request: Request) -> Case:
    body = request.body if request.body is not None else NOT_SET
    return operation.Case(
        # Persisted methods originate from `case.method` (already `HttpMethod`); on load we trust the file.
        method=cast("HttpMethod", request.method),
        path_parameters=request.path_parameters,
        query=request.query,
        headers=request.headers,
        cookies=request.cookies,
        body=body,
    )


def _replay(ctx: EngineContext, entry: Entry, operation: APIOperation) -> tuple[Case, Response] | None:
    case = _build_case(operation, entry.request)
    kwargs = ctx.get_transport_kwargs(operation=operation)
    try:
        response = case.call(**kwargs)
    except requests.RequestException:
        return None
    return case, response


def _send_without_auth(ctx: EngineContext, case: Case, operation: APIOperation) -> Response | None:
    """Send `case` via a fresh auth-less session so the inference path can fire on 401/403."""
    kwargs = ctx.get_transport_kwargs(operation=operation).copy()
    unauth_session = requests.Session()
    if "verify" in kwargs:
        unauth_session.verify = kwargs["verify"]
    if kwargs.get("cert") is not None:
        unauth_session.cert = kwargs["cert"]
    kwargs["session"] = unauth_session
    try:
        return case.call(**kwargs)
    except requests.RequestException:
        return None


def _active_project_title(ctx: EngineContext) -> str | None:
    """Return the active project's title if it matches a named [[project]] block."""
    title = ctx.schema.raw_schema.get("info", {}).get("title")
    if not isinstance(title, str):
        return None
    parent = ctx.config._get_parent()
    if title in parent.projects.named:
        return title
    return None
