import pytest
from hypothesis import HealthCheck, Phase, settings
from hypothesis.errors import InvalidDefinition

import schemathesis
from schemathesis.config import OperationConfig
from schemathesis.core.errors import NoLinksFound
from schemathesis.core.failures import FailureGroup
from schemathesis.generation.modes import GenerationMode
from schemathesis.generation.stateful.state_machine import DEFAULT_STATE_MACHINE_SETTINGS, StepOutput
from schemathesis.specs.openapi.stateful import make_response_filter, match_status_code
from test.apps.catalog.openapi import users as openapi_users
from test.apps.catalog.openapi.modifiers.stateful import NoMergeBody


@pytest.mark.parametrize(
    ("response_status", "filter_value", "matching"),
    [
        (200, 200, True),
        (200, 201, False),
        (200, "20X", True),
    ],
)
def test_match_status_code(response_status, filter_value, matching, response_factory):
    # When the response has `response_status` status
    response = response_factory.requests(status_code=response_status)
    # And the filter should filter by `filter_value`
    filter_function = match_status_code(filter_value)
    assert filter_function.__name__ == f"match_{filter_value}_response"
    # Then that response should match or not depending on the `matching` value
    assert filter_function(StepOutput(response, None)) is matching


@pytest.mark.parametrize(
    ("response_status", "status_codes", "matching"),
    [
        (202, (200, "default"), True),
        (200, (200, "default"), False),
        (200, ("20X", "default"), False),
        (210, ("20X", "default"), True),
    ],
)
def test_default_status_code(response_status, status_codes, matching, response_factory):
    response = response_factory.requests(status_code=response_status)
    filter_function = make_response_filter("default", status_codes)
    assert filter_function(StepOutput(response, None)) is matching


def test_custom_rule(ctx, testdir):
    api = ctx.openapi.apps.success()
    # When the state machine contains a failing rule that does not expect `Case`
    testdir.make_test(
        f"""
from hypothesis.stateful import initialize, rule

schema.config.update(base_url="{api.base_url}/api")

class APIWorkflow(schema.as_state_machine()):

    def validate_response(self, response, case):
        pass

    @rule(data=st.just("foo"))
    def some(self, data):
        assert 0

TestStateful = APIWorkflow.TestCase
""",
    )
    result = testdir.runpytest()
    # Then the reproducing steps should be correctly displayed
    result.assert_outcomes(failed=1)
    result.stdout.re_match_lines([r".*state.some\(data='foo'\)"])


# With the following tests we try to uncover a bug that requires multiple steps with a shared step
# There are three operations:
#   1. Create user via first & last name
#   2. Get user's details - returns id & full name
#   3. Update user - update first & last name
# The problem is that (3) allows you to update a user with non string last name, which is not detectable in its output.
# However, (2) will fail when it will try to create a full name. Reproducing this bug requires 3 steps:
#   1. Create a user
#   2. Update the user with an invalid last name
#   3. Get info about this user


def test_hidden_failure(ctx, testdir):
    api = ctx.openapi.apps.users_crud()
    # When we run test as a state machine
    testdir.make_test(
        f"""
schema.config.update(base_url="{api.base_url}")
schema.config.generation.update(modes=[GenerationMode.POSITIVE])
TestStateful = schema.as_state_machine().TestCase
TestStateful.settings = settings(
    max_examples=2000,
    deadline=None,
    suppress_health_check=list(HealthCheck),
    phases=[Phase.generate],
    stateful_step_count=3  # There is no need for longer sequences to uncover the bug
)
""",
        schema=api.spec,
    )
    result = testdir.runpytest()
    # Then it should be able to find a hidden error:
    result.assert_outcomes(failed=1)
    # And there should be cURL command to reproduce the error in the GET call
    result.stdout.re_match_lines([rf".+curl -X GET '{api.base_url}/users/\w+.+"])


def removeprefix(value: str, prefix: str) -> str:
    if value.startswith(prefix):
        return value[len(prefix) :]
    return value


@pytest.mark.parametrize(
    ("transport", "factory"),
    [
        ("wsgi", openapi_users.crud),
        ("asgi", openapi_users.crud_asgi),
    ],
)
def test_hidden_failure_app(transport, factory):
    app = factory()

    if transport == "asgi":
        schema = schemathesis.openapi.from_asgi("/openapi.json", app=app.server)
        schema.raw_schema["paths"]["/users/"]["post"]["responses"]["201"]["links"] = {
            "GET /users/{user_id}": {
                "parameters": {
                    "path.user_id": "$response.body#/id",
                    "query.uid": "$response.body#/id",
                },
                "operationId": "get_user_users__user_id__get",
            },
            "PATCH /users/{user_id}": {
                "parameters": {"user_id": "$response.body#/id"},
                "operationId": "update_user_users__user_id__patch",
            },
        }
        schema.raw_schema["paths"]["/users/{user_id}"]["get"]["responses"]["404"] = {"description": "Not found"}
        schema.raw_schema["paths"]["/users/{user_id}"]["patch"]["responses"]["404"] = {"description": "Not found"}
        schema.raw_schema["paths"]["/users/{user_id}"]["get"]["responses"]["200"]["links"] = {
            "#/paths/~1users~1{user_id}/patch": {
                "parameters": {"user_id": "$response.body#/id"},
                "requestBody": {"first_name": "foo", "last_name": "bar"},
                "operationRef": "#/paths/~1users~1{user_id}/patch",
            }
        }
    else:
        schema = schemathesis.openapi.from_wsgi("/openapi.json", app=app.server)

    schema.config.generation.update(modes=[GenerationMode.POSITIVE])
    state_machine = schema.as_state_machine()

    with pytest.raises(TypeError, match="can only concatenate str"):
        state_machine.run(
            settings=settings(
                max_examples=2000,
                deadline=None,
                suppress_health_check=list(HealthCheck),
                phases=[Phase.generate],
                stateful_step_count=3,
            )
        )


def test_step_override(ctx, testdir):
    # See GH-970
    api = ctx.openapi.apps.users_crud()
    # When the user overrides the `step` method
    testdir.make_test(
        f"""
schema.config.update(base_url="{api.base_url}")

class APIWorkflow(schema.as_state_machine()):

    def step(self, case, previous=None):
        raise ValueError("ERROR FOUND!")

TestStateful = APIWorkflow.TestCase
TestStateful.settings = settings(
    max_examples=1,
    deadline=None,
    derandomize=True,
    suppress_health_check=list(HealthCheck),
)
""",
        schema=api.spec,
    )
    result = testdir.runpytest()
    # Then it should be overridden
    result.assert_outcomes(failed=1)
    # And the placed error should pop up to indicate that the overridden code is called
    result.stdout.re_match_lines([r".+ValueError: ERROR FOUND!"])


def test_trimmed_output(ctx, testdir):
    api = ctx.openapi.apps.multiple_failures()
    # When an issue is found
    testdir.make_test(
        f"""
schema.config.update(base_url="{api.base_url}")

TestStateful = schema.as_state_machine().TestCase
""",
        schema=api.spec,
    )
    result = testdir.runpytest("--tb=short")
    result.assert_outcomes(failed=1)
    # Then internal frames should not appear after the "Falsifying example" block
    assert " in step" not in result.stdout.str()


def test_no_transitions_error(ctx):
    api = ctx.openapi.apps.success()
    schema = schemathesis.openapi.from_dict(api.spec)
    state_machine_cls = schema.as_state_machine()

    with pytest.raises(NoLinksFound):
        state_machine_cls()


def test_settings_error(ctx):
    api = ctx.openapi.apps.users_crud()
    schema = schemathesis.openapi.from_dict(api.spec)

    class Workflow(schema.as_state_machine()):
        settings = settings(max_examples=5)

    with pytest.raises(InvalidDefinition):
        Workflow()


@pytest.mark.parametrize("merge_body", [True, False])
def test_dynamic_body(merge_body, ctx):
    modifiers = () if merge_body else (NoMergeBody(),)
    api = ctx.openapi.apps.stateful_users(*modifiers)
    schema = schemathesis.openapi.from_wsgi("/openapi.json", app=api.wsgi_app)
    schema.config.generation.update(modes=[GenerationMode.POSITIVE])
    state_machine = schema.as_state_machine()

    state_machine.run(
        settings=settings(
            max_examples=100,
            deadline=None,
            suppress_health_check=list(HealthCheck),
            phases=[Phase.generate],
            stateful_step_count=2,
        )
    )


def test_custom_config_in_test_case(ctx):
    api = ctx.openapi.apps.stateful_users()
    schema = schemathesis.openapi.from_wsgi("/openapi.json", app=api.wsgi_app)
    settings = schema.as_state_machine().TestCase.settings
    for key, value in DEFAULT_STATE_MACHINE_SETTINGS.__dict__.items():
        assert getattr(settings, key) == value


def test_passing_transport_kwargs(ctx, mocker):
    api = ctx.openapi.apps.users_crud()
    schema = schemathesis.openapi.from_dict(api.spec)
    schema.config.update(base_url=api.base_url)

    mocker.patch(
        "schemathesis.specs.openapi.checks.get_security_parameters",
        return_value=[{"name": "token", "required": True, "in": "query"}],
    )
    mocked = mocker.patch("schemathesis.specs.openapi.checks._contains_auth")

    headers = {"Authorization": "Bearer SECRET!", "Content-Type": "application/json"}
    kwargs = {"verify": False, "headers": headers}

    # State machine should properly pass transport kwargs to `validate_response`
    class APIWorkflow(schema.as_state_machine()):
        def before_call(self, case) -> None:
            case.body = {"first_name": "foo", "last_name": "bar"}
            case.query = {}

        def get_call_kwargs(self, case):
            return kwargs

    try:
        APIWorkflow.run()
    except FailureGroup:
        pass

    assert mocked.call_args.args[0]._transport_kwargs == kwargs


@pytest.mark.parametrize(
    ("disabled_op", "expected_rules"),
    [
        (
            "PATCH /users/{user_id}",
            [
                "PATCH_users_user_id___200_GetUserById__GET_users_user_id_",
                "POST_users___201_GetUserByUserId__GET_users_user_id_",
                "RANDOM__POST_users_",
            ],
        ),
        (
            "POST /users/",
            [
                "GET_users_user_id___200_UpdateUserById__PATCH_users_user_id_",
                "PATCH_users_user_id___200_GetUserById__GET_users_user_id_",
                "POST_users___201_GetUserByUserId__GET_users_user_id_",
                "POST_users___201_UpdateUserById__PATCH_users_user_id_",
            ],
        ),
    ],
    ids=["transition-target", "root"],
)
def test_stateful_disabled_for_op_skips_rules(ctx, disabled_op, expected_rules):
    api = ctx.openapi.apps.users_crud()
    schema = schemathesis.openapi.from_dict(api.spec)
    schema.config.operations.operations.append(
        OperationConfig.from_dict({"include-name": disabled_op, "phases": {"stateful": {"enabled": False}}})
    )

    # No rule should execute the disabled op — root or incoming-transition.
    # Outgoing transitions whose source is the disabled op stay (their target is still enabled).
    state_machine = schema.as_state_machine()
    assert (
        sorted(name for name, value in state_machine.__dict__.items() if hasattr(value, "hypothesis_stateful_rule"))
        == expected_rules
    )


_ID_OBJECT = {"type": "object", "properties": {"id": {"type": "integer"}}, "required": ["id"]}


def test_fk_consumer_with_producer_stays_root(ctx):
    # POST /photos consumes Album.id via body `albumId`; Album has a producer (POST /albums),
    # so the chain can reach the consumer through a Link. The consumer stays in the reliable
    # root set, letting the state machine also start chains from it.
    schema = ctx.openapi.load_schema(
        {
            "/albums": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {"type": "object", "properties": {"name": {"type": "string"}}}
                            }
                        },
                    },
                    "responses": {"201": {"content": {"application/json": {"schema": _ID_OBJECT}}}},
                }
            },
            "/photos": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "albumId": {"type": "integer"},
                                        "title": {"type": "string"},
                                    },
                                    "required": ["albumId", "title"],
                                }
                            }
                        },
                    },
                    "responses": {"201": {"content": {"application/json": {"schema": _ID_OBJECT}}}},
                }
            },
            "/photos/{photoId}": {
                "get": {
                    "parameters": [{"in": "path", "name": "photoId", "required": True, "schema": {"type": "integer"}}],
                    "responses": {"200": {"content": {"application/json": {"schema": _ID_OBJECT}}}},
                }
            },
        }
    )

    state_machine = schema.as_state_machine()
    assert sorted(
        name for name, value in state_machine.__dict__.items() if hasattr(value, "hypothesis_stateful_rule")
    ) == [
        "POST_albums___201_PostAlbum__POST_photos",
        "POST_photos___201_GetPhoto__GET_photos_photoId_",
        "RANDOM__POST_albums",
        "RANDOM__POST_photos",
    ]


def test_unsatisfiable_fk_consumer_not_classified_as_root(ctx):
    # POST /photos consumes Album.id via body `albumId`, but no operation produces Album.
    # The clean list endpoint keeps the reliable set non-empty so the safety-net fallback
    # doesn't fire, letting us observe that the unsatisfiable consumer was demoted.
    schema = ctx.openapi.load_schema(
        {
            "/photos": {
                "get": {
                    "responses": {
                        "200": {"content": {"application/json": {"schema": {"type": "array", "items": _ID_OBJECT}}}}
                    }
                },
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "albumId": {"type": "integer"},
                                        "title": {"type": "string"},
                                    },
                                    "required": ["albumId", "title"],
                                }
                            }
                        },
                    },
                    "responses": {"201": {"content": {"application/json": {"schema": _ID_OBJECT}}}},
                },
            },
            "/photos/{photoId}": {
                "get": {
                    "parameters": [{"in": "path", "name": "photoId", "required": True, "schema": {"type": "integer"}}],
                    "responses": {"200": {"content": {"application/json": {"schema": _ID_OBJECT}}}},
                }
            },
        }
    )

    state_machine = schema.as_state_machine()
    assert sorted(
        name for name, value in state_machine.__dict__.items() if hasattr(value, "hypothesis_stateful_rule")
    ) == [
        "GET_photos___200_GetPhoto__GET_photos_photoId_",
        "POST_photos___201_GetPhoto__GET_photos_photoId_",
        "RANDOM__GET_photos",
    ]


def test_self_referential_fk_creator_remains_root(ctx):
    person_req = {
        "type": "object",
        "properties": {"id": {"type": "integer"}, "name": {"type": "string"}},
        "required": ["id", "name"],
    }
    schema = ctx.openapi.load_schema(
        {
            "/person": {
                "post": {
                    "requestBody": {"required": True, "content": {"application/json": {"schema": person_req}}},
                    "responses": {"201": {"content": {"application/json": {"schema": _ID_OBJECT}}}},
                }
            },
            "/persons": {
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {"application/json": {"schema": {"type": "array", "items": person_req}}},
                    },
                    "responses": {"201": {"content": {"application/json": {"schema": _ID_OBJECT}}}},
                }
            },
            "/person/{id}": {
                "get": {
                    "parameters": [{"in": "path", "name": "id", "required": True, "schema": {"type": "integer"}}],
                    "responses": {"200": {"content": {"application/json": {"schema": _ID_OBJECT}}}},
                }
            },
        }
    )
    state_machine = schema.as_state_machine()
    rule_names = sorted(
        name for name, value in state_machine.__dict__.items() if hasattr(value, "hypothesis_stateful_rule")
    )
    assert "RANDOM__POST_person" in rule_names, rule_names
