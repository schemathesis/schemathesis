import schemathesis
from schemathesis.config._auth import AuthConfig
from schemathesis.core.error_feedback import (
    ErrorFeedbackStore,
    Observation,
    ObservationKind,
    RequiresAuthPayload,
)
from schemathesis.core.parameters import ParameterLocation
from schemathesis.engine import events
from schemathesis.engine.recorder import ScenarioRecorder
from schemathesis.engine.run import PhaseName
from schemathesis.specs.openapi.auth_inference import record_auth_inference
from test.apps.catalog.openapi.modifiers.stateful import RequireBearerAuth
from test.apps.catalog.openapi.modifiers.under_declared_security import (
    DeclareSecurity,
    DocumentResponseStatus,
    RespondWithStatus,
)

VALID_TOKEN = "real-token"


def _load_schema_with_creds(schema_url, *, token=VALID_TOKEN):
    schema = schemathesis.openapi.from_url(schema_url)
    schema.config.auth = AuthConfig.from_dict({"openapi": {"BearerAuth": {"bearer": token}}})
    return schema


def _make_auth_observation(operation_label: str = "POST /users") -> Observation:
    return Observation(
        operation_label=operation_label,
        location=ParameterLocation.PATH,
        parameter_path=(),
        kind=ObservationKind.REQUIRES_AUTHENTICATION,
        raw_message="401/403 fixed by BearerAuth",
        payload=RequiresAuthPayload(scheme_name="BearerAuth"),
    )


def test_requires_authentication_kind_round_trips_through_store():
    store = ErrorFeedbackStore()
    observation = _make_auth_observation()
    store.record(observation)
    stored = store.observations(
        operation_label="POST /users",
        location=ParameterLocation.PATH,
        min_count=1,
    )
    assert stored == (observation,)


def test_record_auth_inference_records_observation_and_mutates_operation(ctx):
    api = ctx.openapi.apps.under_declared_security()
    schema = _load_schema_with_creds(api.schema_url)
    operation = next(result.ok() for result in schema.get_all_operations())
    case = operation.Case(method="GET")
    response_401 = case.call()
    assert response_401.status_code == 401

    store = ErrorFeedbackStore()
    recorder = ScenarioRecorder(label="auth-inference-test")
    recorder.record_case(parent_id=None, case=case, transition=None, is_transition_applied=False)
    recorder.record_response(case_id=case.id, response=response_401)

    record_auth_inference(
        store=store,
        recorder=recorder,
        case=case,
        response=response_401,
        transport_kwargs={},
    )

    observations = store.observations(
        operation_label=operation.label,
        location=ParameterLocation.PATH,
        min_count=1,
    )
    assert observations == (
        Observation(
            operation_label=operation.label,
            location=ParameterLocation.PATH,
            parameter_path=(),
            kind=ObservationKind.REQUIRES_AUTHENTICATION,
            raw_message="401 fixed by BearerAuth",
            payload=RequiresAuthPayload(scheme_name="BearerAuth"),
        ),
    )
    assert operation.schema._inferred_security[operation.label] == [{"BearerAuth": []}]
    assert "security" not in operation.definition.raw


def test_record_auth_inference_skips_non_auth_status_codes(ctx, response_factory):
    api = ctx.openapi.apps.under_declared_security()
    schema = _load_schema_with_creds(api.schema_url)
    operation = next(result.ok() for result in schema.get_all_operations())
    case = operation.Case(method="GET")
    response_200 = response_factory.requests(status_code=200)

    store = ErrorFeedbackStore()
    recorder = ScenarioRecorder(label="auth-inference-test")

    record_auth_inference(
        store=store,
        recorder=recorder,
        case=case,
        response=response_200,
        transport_kwargs={},
    )

    assert (
        store.observations(
            operation_label=operation.label,
            location=ParameterLocation.PATH,
            min_count=1,
        )
        == ()
    )
    assert "security" not in operation.definition.raw


def test_record_auth_inference_skips_when_operation_declares_security(ctx):
    api = ctx.openapi.apps.under_declared_security(DeclareSecurity())
    schema = _load_schema_with_creds(api.schema_url)
    operation = next(result.ok() for result in schema.get_all_operations())
    case = operation.Case(method="GET")
    response_401 = case.call()
    assert response_401.status_code == 401
    original_security = list(operation.definition.raw.get("security", []))

    store = ErrorFeedbackStore()
    recorder = ScenarioRecorder(label="auth-inference-test")

    record_auth_inference(
        store=store,
        recorder=recorder,
        case=case,
        response=response_401,
        transport_kwargs={},
    )

    assert (
        store.observations(
            operation_label=operation.label,
            location=ParameterLocation.PATH,
            min_count=1,
        )
        == ()
    )
    assert operation.definition.raw.get("security", []) == original_security
    assert operation.label not in operation.schema._inferred_security


def test_record_auth_inference_idempotent_per_operation(ctx):
    api = ctx.openapi.apps.under_declared_security()
    schema = _load_schema_with_creds(api.schema_url)
    operation = next(result.ok() for result in schema.get_all_operations())
    case = operation.Case(method="GET")
    response_401 = case.call()

    store = ErrorFeedbackStore()
    recorder = ScenarioRecorder(label="auth-inference-test")

    for _ in range(2):
        record_auth_inference(
            store=store,
            recorder=recorder,
            case=operation.Case(method="GET"),
            response=response_401,
            transport_kwargs={},
        )

    observations = store.observations(
        operation_label=operation.label,
        location=ParameterLocation.PATH,
        min_count=1,
    )
    assert observations == (
        Observation(
            operation_label=operation.label,
            location=ParameterLocation.PATH,
            parameter_path=(),
            kind=ObservationKind.REQUIRES_AUTHENTICATION,
            raw_message="401 fixed by BearerAuth",
            payload=RequiresAuthPayload(scheme_name="BearerAuth"),
        ),
    )
    assert operation.schema._inferred_security[operation.label] == [{"BearerAuth": []}]
    assert "security" not in operation.definition.raw


def test_record_auth_inference_no_configured_schemes_skips(ctx):
    api = ctx.openapi.apps.under_declared_security()
    schema = schemathesis.openapi.from_url(api.schema_url)
    operation = next(result.ok() for result in schema.get_all_operations())
    case = operation.Case(method="GET")
    response_401 = case.call()
    assert response_401.status_code == 401

    store = ErrorFeedbackStore()
    recorder = ScenarioRecorder(label="auth-inference-test")

    record_auth_inference(
        store=store,
        recorder=recorder,
        case=case,
        response=response_401,
        transport_kwargs={},
    )

    assert (
        store.observations(
            operation_label=operation.label,
            location=ParameterLocation.PATH,
            min_count=1,
        )
        == ()
    )


def test_record_auth_inference_retry_still_401_does_not_record(ctx):
    api = ctx.openapi.apps.under_declared_security()
    schema = _load_schema_with_creds(api.schema_url, token="WRONG-token")
    operation = next(result.ok() for result in schema.get_all_operations())
    case = operation.Case(method="GET")
    response_401 = case.call()
    assert response_401.status_code == 401

    store = ErrorFeedbackStore()
    recorder = ScenarioRecorder(label="auth-inference-test")

    record_auth_inference(
        store=store,
        recorder=recorder,
        case=case,
        response=response_401,
        transport_kwargs={},
    )

    assert (
        store.observations(
            operation_label=operation.label,
            location=ParameterLocation.PATH,
            min_count=1,
        )
        == ()
    )


def test_engine_run_attaches_token_after_inference(ctx):
    api = ctx.openapi.apps.under_declared_security()
    schema = _load_schema_with_creds(api.schema_url)

    successful_protected_statuses: list[int] = []
    for event in schemathesis.engine.from_schema(schema).execute():
        if not isinstance(event, (events.ScenarioFinished, events.FuzzScenarioFinished)):
            continue
        for case_id, interaction in event.recorder.interactions.items():
            if interaction.response is None:
                continue
            if not (200 <= interaction.response.status_code < 300):
                continue
            case_node = event.recorder.cases.get(case_id)
            if case_node is None:
                continue
            generated_case = case_node.value
            if generated_case.operation.label == "GET /protected" and generated_case.method == "GET":
                successful_protected_statuses.append(interaction.response.status_code)

    assert successful_protected_statuses, "expected at least one 2xx /protected response after inference"


def test_record_auth_inference_skips_when_retry_returns_5xx(ctx):
    api = ctx.openapi.apps.under_declared_security(RespondWithStatus(500))
    schema = _load_schema_with_creds(api.schema_url)
    operation = next(result.ok() for result in schema.get_all_operations())
    case = operation.Case(method="GET")
    response_401 = case.call()
    assert response_401.status_code == 401

    store = ErrorFeedbackStore()
    recorder = ScenarioRecorder(label="auth-inference-test")

    record_auth_inference(
        store=store,
        recorder=recorder,
        case=case,
        response=response_401,
        transport_kwargs={},
    )

    assert (
        store.observations(
            operation_label=operation.label,
            location=ParameterLocation.PATH,
            min_count=1,
        )
        == ()
    )


def test_stateful_phase_attaches_token_after_inference(ctx):
    api = ctx.openapi.apps.stateful_users(RequireBearerAuth())
    schema = _load_schema_with_creds(api.schema_url)
    schema.config.phases.examples.enabled = False
    schema.config.phases.coverage.enabled = False
    schema.config.phases.fuzzing.enabled = False

    saw_2xx_in_stateful_phase = False
    for event in schemathesis.engine.from_schema(schema).execute():
        if not isinstance(event, events.ScenarioFinished):
            continue
        if event.phase != PhaseName.STATEFUL_TESTING:
            continue
        for interaction in event.recorder.interactions.values():
            if interaction.response is None:
                continue
            if 200 <= interaction.response.status_code < 300:
                saw_2xx_in_stateful_phase = True
                break

    assert saw_2xx_in_stateful_phase, "expected at least one 2xx during stateful phase after inference"


def test_send_with_scheme_forwards_wsgi_app_to_retry(ctx):
    api = ctx.openapi.apps.under_declared_security()
    schema = schemathesis.openapi.from_wsgi("/openapi.json", api.wsgi_app)
    schema.config.auth = AuthConfig.from_dict({"openapi": {"BearerAuth": {"bearer": VALID_TOKEN}}})
    operation = next(result.ok() for result in schema.get_all_operations())
    case = operation.Case(method="GET")
    response_401 = case.call()
    assert response_401.status_code == 401

    store = ErrorFeedbackStore()
    recorder = ScenarioRecorder(label="auth-inference-test")
    record_auth_inference(
        store=store,
        recorder=recorder,
        case=case,
        response=response_401,
        transport_kwargs={},
    )

    assert operation.schema._inferred_security[operation.label] == [{"BearerAuth": []}]
    assert "security" not in operation.definition.raw


def test_record_auth_inference_skips_for_non_openapi_schema(ctx, response_factory):
    schema = schemathesis.graphql.from_url(ctx.graphql.apps.books().schema_url)
    operation = schema["Query"]["getBooks"]
    case = operation.Case(body="{ getBooks { id } }")
    response_401 = response_factory.requests(status_code=401)

    store = ErrorFeedbackStore()
    recorder = ScenarioRecorder(label="auth-inference-test")
    record_auth_inference(
        store=store,
        recorder=recorder,
        case=case,
        response=response_401,
        transport_kwargs={},
    )

    assert (
        store.observations(
            operation_label=operation.label,
            location=ParameterLocation.PATH,
            min_count=1,
        )
        == ()
    )


def test_record_auth_inference_skips_on_retry_network_error(ctx, app_runner, response_factory):
    api = ctx.openapi.apps.under_declared_security()
    schema = _load_schema_with_creds(api.schema_url)
    operation = next(result.ok() for result in schema.get_all_operations())
    operation.base_url = f"http://127.0.0.1:{app_runner.unused_port()}"
    case = operation.Case(method="GET")
    response_401 = response_factory.requests(status_code=401)

    store = ErrorFeedbackStore()
    recorder = ScenarioRecorder(label="auth-inference-test")
    record_auth_inference(
        store=store,
        recorder=recorder,
        case=case,
        response=response_401,
        transport_kwargs={},
    )

    assert (
        store.observations(
            operation_label=operation.label,
            location=ParameterLocation.PATH,
            min_count=1,
        )
        == ()
    )
    assert "security" not in operation.definition.raw
    assert operation.label not in operation.schema._inferred_security


def test_record_auth_inference_skips_when_provider_raises(ctx, response_factory):
    api = ctx.openapi.apps.under_declared_security()
    schema = _load_schema_with_creds(api.schema_url)
    operation = next(result.ok() for result in schema.get_all_operations())
    case = operation.Case(method="GET")
    response_401 = response_factory.requests(status_code=401)

    schema.config.auth = AuthConfig.from_dict({"openapi": {"BearerAuth": {"api_key": "secret"}}})

    store = ErrorFeedbackStore()
    recorder = ScenarioRecorder(label="auth-inference-test")
    record_auth_inference(
        store=store,
        recorder=recorder,
        case=case,
        response=response_401,
        transport_kwargs={},
    )

    assert (
        store.observations(
            operation_label=operation.label,
            location=ParameterLocation.PATH,
            min_count=1,
        )
        == ()
    )
    assert operation.label not in operation.schema._inferred_security


def test_record_auth_inference_skips_when_retry_returns_undocumented_status(ctx):
    api = ctx.openapi.apps.under_declared_security(RespondWithStatus(404))
    schema = _load_schema_with_creds(api.schema_url)
    operation = next(result.ok() for result in schema.get_all_operations())
    case = operation.Case(method="GET")
    response_401 = case.call()
    assert response_401.status_code == 401

    store = ErrorFeedbackStore()
    recorder = ScenarioRecorder(label="auth-inference-test")
    record_auth_inference(
        store=store,
        recorder=recorder,
        case=case,
        response=response_401,
        transport_kwargs={},
    )

    assert (
        store.observations(
            operation_label=operation.label,
            location=ParameterLocation.PATH,
            min_count=1,
        )
        == ()
    )
    assert operation.label not in operation.schema._inferred_security


def test_record_auth_inference_records_documented_4xx_as_auth_success(ctx):
    api = ctx.openapi.apps.under_declared_security(
        RespondWithStatus(422),
        DocumentResponseStatus(422),
    )
    schema = _load_schema_with_creds(api.schema_url)
    operation = next(result.ok() for result in schema.get_all_operations())
    case = operation.Case(method="GET")
    response_401 = case.call()
    assert response_401.status_code == 401

    store = ErrorFeedbackStore()
    recorder = ScenarioRecorder(label="auth-inference-test")
    record_auth_inference(
        store=store,
        recorder=recorder,
        case=case,
        response=response_401,
        transport_kwargs={},
    )

    assert operation.schema._inferred_security[operation.label] == [{"BearerAuth": []}]
