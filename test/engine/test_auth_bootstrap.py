import pytest

import schemathesis
from schemathesis.config import HttpBearerAuthConfig
from schemathesis.engine import Status, events
from schemathesis.engine.run import PhaseName
from schemathesis.generation import GenerationMode
from test.utils import EventStream


def _run(schema, **kwargs):
    return EventStream(schema, phases=[PhaseName.FUZZING], max_examples=5, **kwargs).execute()


def _bootstrap(stream):
    return stream.find(events.PhaseFinished, phase=lambda phase: phase.name is PhaseName.AUTH_BOOTSTRAP)


def _statuses(stream, label):
    return [
        interaction.response.status_code
        for event in stream.find_all(events.ScenarioFinished, label=label)
        if event.phase is not PhaseName.AUTH_BOOTSTRAP
        for interaction in event.recorder.interactions.values()
    ]


@pytest.mark.parametrize("transport", ["http", "wsgi"])
def test_signed_up_session_authenticates_protected_operations(ctx, transport):
    api = ctx.openapi.apps.sign_up_and_login()
    if transport == "http":
        schema = schemathesis.openapi.from_url(api.schema_url)
    else:
        schema = schemathesis.openapi.from_wsgi("/openapi.json", api.wsgi_app)
    stream = _run(schema)
    assert (_bootstrap(stream).status, _bootstrap(stream).payload.failure_stage) == (Status.SUCCESS, None)
    assert 200 in _statuses(stream, "GET /orders")


def test_fuzzed_login_reuses_signed_up_credentials(ctx):
    api = ctx.openapi.apps.sign_up_and_login()
    stream = _run(schemathesis.openapi.from_url(api.schema_url), modes=[GenerationMode.POSITIVE], seed=1)
    assert 200 in _statuses(stream, "POST /auth/login")


def test_sign_up_sends_every_profile_field(ctx):
    api = ctx.openapi.apps.sign_up_and_login()
    _run(schemathesis.openapi.from_url(api.schema_url))
    sign_up = next(request.json() for request in api.requests if request.path == "/auth/register")
    assert (sorted(sign_up), bool(sign_up["firstName"].strip())) == (
        ["email", "firstName", "password", "role", "username"],
        True,
    )


# Operations an ordinary account may not touch open up only to the most privileged role the sign-up offers.
def test_sign_up_registers_the_most_privileged_role(ctx):
    api = ctx.openapi.apps.sign_up_and_login("admin-only")
    stream = _run(schemathesis.openapi.from_url(api.schema_url))
    sign_up = next(request.json() for request in api.requests if request.path == "/auth/register")
    assert (sign_up["role"], 200 in _statuses(stream, "GET /orders")) == ("ADMIN", True)


def test_sign_up_keeps_generated_role_when_no_privileged_role_is_offered(ctx):
    api = ctx.openapi.apps.sign_up_and_login("ordinary-role")
    _run(schemathesis.openapi.from_url(api.schema_url))
    sign_up = next(request.json() for request in api.requests if request.path == "/auth/register")
    assert sign_up["role"] == "MEMBER"


def test_register_and_login_are_reported_as_scenarios(ctx):
    api = ctx.openapi.apps.sign_up_and_login()
    stream = _run(schemathesis.openapi.from_url(api.schema_url))
    scenarios = stream.find_all(events.ScenarioFinished, phase=PhaseName.AUTH_BOOTSTRAP)
    assert [
        (
            event.label,
            event.status,
            [interaction.response.status_code for interaction in event.recorder.interactions.values()],
        )
        for event in scenarios
    ] == [
        ("POST /auth/register", Status.SUCCESS, [201]),
        ("POST /auth/login", Status.SUCCESS, [200]),
    ]


@pytest.mark.parametrize(
    ("behavior", "expected"),
    [
        ("register-rejects", ("register", 400)),
        ("login-rejects", ("login", 401)),
        ("no-token", ("extract", None)),
        ("unsatisfiable-password", ("sign-up", None)),
    ],
)
def test_bootstrap_failures(ctx, behavior, expected):
    api = ctx.openapi.apps.sign_up_and_login(behavior)
    payload = _bootstrap(_run(schemathesis.openapi.from_url(api.schema_url))).payload
    assert (payload.status, payload.failure_stage, payload.status_code) == (Status.ERROR, *expected)


def _configure_scheme(schema):
    schema.config.auth.openapi.schemes["bearerAuth"] = HttpBearerAuthConfig(bearer="token-0")
    return {}


def _programmatic_auth(schema):
    @schema.auth()
    class Auth:
        def get(self, case, context):
            return "token-0"

        def set(self, case, data, context):
            case.headers = {"Authorization": f"Bearer {data}"}

    return {}


@pytest.mark.parametrize(
    "supply",
    [_configure_scheme, _programmatic_auth, lambda schema: {"headers": {"Authorization": "Bearer token-0"}}],
    ids=["openapi-scheme", "programmatic", "header"],
)
def test_bootstrap_skipped_when_auth_is_supplied(ctx, supply):
    api = ctx.openapi.apps.sign_up_and_login()
    schema = schemathesis.openapi.from_url(api.schema_url)
    stream = _run(schema, **supply(schema))
    assert (_bootstrap(stream).status, stream.find_all(events.ScenarioStarted, phase=PhaseName.AUTH_BOOTSTRAP)) == (
        Status.SKIP,
        [],
    )


def test_no_flow_is_a_silent_success(ctx):
    api = ctx.openapi.apps.success()
    payload = _bootstrap(_run(schemathesis.openapi.from_url(api.schema_url))).payload
    assert (payload.status, payload.spec) == (Status.SUCCESS, None)


def test_unreachable_sign_up_is_a_register_failure(ctx):
    api = ctx.openapi.apps.sign_up_and_login()
    schema = schemathesis.openapi.from_url(api.schema_url)
    schema.config.update(base_url="http://127.0.0.1:1")
    payload = _bootstrap(_run(schema)).payload
    assert (payload.status, payload.failure_stage, payload.status_code) == (Status.ERROR, "register", None)
