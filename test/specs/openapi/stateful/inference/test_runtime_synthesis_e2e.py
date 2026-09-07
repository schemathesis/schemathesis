import threading

import hypothesis
import schemathesis
from schemathesis.config import SchemathesisConfig
from schemathesis.core.result import Ok
from schemathesis.core.transport import Response
from schemathesis.engine.context import EngineContext
from schemathesis.engine.run import Phase, PhaseName, stateful
from schemathesis.generation.modes import GenerationMode
from schemathesis.generation.stateful.state_machine import StepOutput
from schemathesis.specs.openapi.expressions import MultiMatch
from schemathesis.specs.openapi.stateful.links import SCHEMATHESIS_LINK_EXTENSION, OpenApiLink
from test.utils import flaky


def test_runtime_synthesis_produces_extractable_link(ctx, response_factory):
    api = ctx.openapi.apps.paged_response_albums()
    schema = schemathesis.openapi.from_url(api.schema_url)

    data_source = schema.analysis.extra_data_source
    operation = schema["/api/albums"]["GET"]
    case = operation.Case()
    raw = response_factory.requests(
        status_code=200,
        content=b'{"content": [{"id": "album-a1b2c3"}, {"id": "album-d4e5f6"}], "totalElements": 2}',
    )
    data_source.record_observed_body(operation=operation, response=Response.from_requests(raw, True), case=case)

    assert schema.analysis.checkpoint() is True

    # `checkpoint()` only invalidates caches; link injection happens on the next
    # `as_state_machine()` call. Rebuild the state machine class so `inject_links()`
    # writes the fresh inferred links into the response definitions.
    schema.as_state_machine()

    response = operation.responses.get("200")
    links = response.definition.get(schema.adapter.links_keyword, {})
    inferred = [
        link
        for link in links.values()
        if isinstance(link, dict)
        and link.get(SCHEMATHESIS_LINK_EXTENSION, {}).get("source") == "dependency-analysis"
    ]
    assert inferred, "expected at least one dependency-analysis inferred link"
    relevant = next(
        link
        for link in inferred
        if "albumId" in (link.get("requestBody") or {}) and "/*/" in link["requestBody"]["albumId"]
    )

    link_obj = OpenApiLink(name="X", status_code="200", definition=relevant, source=operation)
    case = operation.Case()
    step_output = StepOutput(response=Response.from_requests(raw, True), case=case)
    extracted = link_obj.extract_body(step_output)
    assert isinstance(extracted.value, Ok)
    body = extracted.value.ok()
    multi = body["albumId"]
    assert isinstance(multi, MultiMatch)
    assert set(multi.values) == {"album-a1b2c3", "album-d4e5f6"}


_SLUGS = {"album-a1b2c3", "album-d4e5f6"}


def _run_stateful(schema, *, seed: int = 42, max_examples: int = 200, max_steps: int = 6):
    """Drive the stateful phase end-to-end against a pre-built schema; return the event stream."""
    stop_event = threading.Event()
    current = schema.config.get_hypothesis_settings()
    new = hypothesis.settings(current, max_examples=max_examples, deadline=None)
    schema.config.get_hypothesis_settings = lambda *_, **__: new
    schema.config.seed = seed
    return list(
        stateful.execute(
            engine=EngineContext(schema=schema, stop_event=stop_event),
            phase=Phase(name=PhaseName.STATEFUL_TESTING, is_enabled=True),
        )
    )


def _photo_post_album_ids(events_list) -> set[str]:
    """Walk SuiteFinished / ScenarioFinished events for recorded POST /api/photos cases."""
    ids: set[str] = set()
    for event in events_list:
        recorder = getattr(event, "recorder", None)
        if recorder is None:
            continue
        for case in recorder.find_all_cases():
            if case.operation.label == "POST /api/photos" and isinstance(case.body, dict):
                album_id = case.body.get("albumId")
                if isinstance(album_id, str):
                    ids.add(album_id)
    return ids


def _build_schema(schema_url: str):
    config = SchemathesisConfig()
    config.projects.override.generation.update(
        modes=[GenerationMode.POSITIVE],
        database="none",
    )
    return schemathesis.openapi.from_url(schema_url, config=config)


@flaky(max_runs=3, min_passes=1)
def test_runtime_synthesis_fires_in_stateful_run(ctx):
    api = ctx.openapi.apps.paged_response_albums()
    schema = _build_schema(api.schema_url)
    events_list = _run_stateful(schema, seed=42, max_examples=200)
    assert _photo_post_album_ids(events_list) & _SLUGS, (
        "expected at least one POST /api/photos to use a real album slug"
    )


def test_runtime_synthesis_skips_author_declared_schema(ctx):
    api = ctx.openapi.apps.paged_response_albums_declared()
    schema = _build_schema(api.schema_url)
    _run_stateful(schema, seed=42, max_examples=200)
    assert schema.analysis.last_overlay == {}, (
        "synthesis must not fire when the spec already declares the response shape"
    )
