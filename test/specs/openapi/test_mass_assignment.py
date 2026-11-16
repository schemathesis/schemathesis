import itertools

import pytest
from flask import jsonify, request

import schemathesis
from schemathesis.config import ChecksConfig, ConfigError, MassAssignmentConfig
from schemathesis.engine import Status
from schemathesis.engine.events import ScenarioFinished
from schemathesis.engine.run import PhaseName
from schemathesis.generation import GenerationMode
from schemathesis.openapi.checks import MassAssignment
from schemathesis.specs.openapi.checks import mass_assignment
from schemathesis.specs.openapi.mass_assignment import (
    _MISSING,
    MARKER_DATE_TIME,
    MARKER_NUMBER,
    PRIVILEGE_FIELDS,
    FieldOrigin,
    MassAssignmentProbe,
    Target,
    choose_value,
    dig,
    evaluate,
    is_identifier,
    matches,
    split_path,
    uniquify,
)
from test.apps.builders import make_flask_app_from_schema
from test.utils import EventStream

USER_SCHEMA = {
    "type": "object",
    "properties": {
        "id": {"type": "integer", "readOnly": True},
        "name": {"type": "string", "minLength": 1, "maxLength": 20},
        "role": {"type": "string", "enum": ["user", "admin"], "readOnly": True},
        "is_admin": {"type": "boolean", "readOnly": True},
    },
    "required": ["name"],
}
USER_INPUT = {
    "type": "object",
    "properties": {"name": {"type": "string", "minLength": 1, "maxLength": 20}},
    "required": ["name"],
}
DEFAULTS = {"role": "user", "is_admin": False}


def _schema(
    request_schema=USER_SCHEMA,
    response_schema=USER_SCHEMA,
    *,
    item_methods=("get", "put", "delete"),
    item_parameter_type="integer",
):
    response = {"description": "OK", "content": {"application/json": {"schema": response_schema}}}
    item = {
        "parameters": [{"name": "user_id", "in": "path", "required": True, "schema": {"type": item_parameter_type}}],
    }
    body = {"required": True, "content": {"application/json": {"schema": request_schema}}}
    for method in item_methods:
        if method == "get":
            item["get"] = {"responses": {"200": response, "404": {"description": "Not found"}}}
        elif method in ("put", "patch"):
            item[method] = {"requestBody": body, "responses": {"200": response, "404": {"description": "Not found"}}}
        elif method == "delete":
            item["delete"] = {"responses": {"204": {"description": "Deleted"}}}
    return {
        "openapi": "3.0.3",
        "info": {"title": "Users", "version": "1"},
        "paths": {
            "/users": {"post": {"requestBody": body, "responses": {"201": response, "400": {"description": "Bad"}}}},
            "/users/{user_id}": item,
        },
    }


def _users_app(
    spec,
    *,
    mode,
    encode=None,
    read_misses=0,
    unique=None,
    probe_status=None,
    location=False,
    string_ids=False,
    require_header=None,
    delete_status=204,
    created_body=True,
):
    """Test API with configurable behavior.

    mode: `vulnerable` stores everything, `secure` ignores extra fields, `strict` rejects them, `echo` returns
    the request without storing it.
    """
    app = make_flask_app_from_schema(spec)
    users: dict = {}
    ids = itertools.count(1)
    misses = {"left": read_misses}
    app.config["users"] = users
    app.config["deleted"] = []
    app.config["requests"] = []
    encode = encode or (lambda record: record)

    @app.before_request
    def guard():
        app.config["requests"].append((request.method, request.path, request.get_json(silent=True)))
        if (
            require_header
            and request.path != "/openapi.json"
            and request.headers.get(require_header[0]) != require_header[1]
        ):
            return jsonify({"detail": "unauthorized"}), 401
        payload = request.get_json(silent=True)
        if probe_status and isinstance(payload, dict) and set(payload) - {"name"}:
            return jsonify({"detail": "blocked"}), probe_status
        return None

    def store(user_id, payload, existing=None):
        user = dict(existing or DEFAULTS)
        if mode == "strict" and set(payload) - {"name"}:
            return None
        for key, value in payload.items():
            if mode == "vulnerable" or key == "name":
                user[key] = value
        user["id"] = user_id
        users[user_id] = user
        return user

    @app.route("/users", methods=["POST"])
    def create():
        payload = request.get_json()
        if unique and any(user.get(unique) == payload.get(unique) for user in users.values()):
            return jsonify({"detail": "duplicate"}), 409
        user_id = next(ids)
        if string_ids:
            user_id = f"u{user_id}"
        if mode == "echo":
            users[user_id] = {"id": user_id, "name": payload.get("name"), **DEFAULTS}
            return jsonify({**payload, "id": user_id}), 201
        user = store(user_id, payload)
        if user is None:
            return jsonify({"detail": "unknown fields"}), 400
        body = encode(user) if created_body else {"status": "created"}
        response = jsonify(body)
        response.status_code = 201
        if location:
            response.headers["Location"] = f"/users/{user_id}"
        return response

    def lookup(raw):
        for key in (raw, int(raw) if raw.isdigit() else None):
            if key in users:
                return key
        return None

    @app.route("/users/<user_id>", methods=["GET"])
    def read(user_id):
        key = lookup(user_id)
        if key is None:
            return jsonify({"detail": "not found"}), 404
        if misses["left"] > 0:
            misses["left"] -= 1
            return jsonify({"detail": "not found"}), 404
        return jsonify(encode(users[key]))

    @app.route("/users/<user_id>", methods=["PUT", "PATCH"])
    def update(user_id):
        key = lookup(user_id)
        if key is None:
            return jsonify({"detail": "not found"}), 404
        user = store(key, request.get_json(), users[key])
        if user is None:
            return jsonify({"detail": "unknown fields"}), 400
        return jsonify(encode(user))

    @app.route("/users/<user_id>", methods=["DELETE"])
    def delete(user_id):
        key = lookup(user_id)
        app.config["deleted"].append(key)
        if delete_status >= 400:
            return jsonify({"detail": "nope"}), delete_status
        users.pop(key, None)
        return "", delete_status

    return app


def _schema_for(app, config=None, operation="POST /users"):
    config = schemathesis.Config.from_dict({"checks": {"mass_assignment": config}}) if config else None
    schema = schemathesis.openapi.from_wsgi("/openapi.json", app=app, config=config)
    return schema.include(name=operation)


def _run(
    app,
    *,
    operation="POST /users",
    checks=(mass_assignment,),
    max_examples=5,
    config=None,
    schema=None,
    modes=(GenerationMode.POSITIVE,),
    **kwargs,
):
    schema = schema or _schema_for(app, config, operation)
    stream = EventStream(
        schema,
        checks=list(checks),
        phases=[PhaseName.FUZZING],
        max_examples=max_examples,
        modes=list(modes),
        **kwargs,
    ).execute()
    return stream.find(ScenarioFinished)


def _failures(event):
    seen = {}
    for checks in event.recorder.checks.values():
        for check in checks:
            if check.failure_info is not None:
                seen[id(check.failure_info.failure)] = check.failure_info.failure
    return list(seen.values())


def _the_failure(event):
    assert event.status == Status.FAILURE
    failures = _failures(event)
    assert failures
    assert all(isinstance(failure, MassAssignment) for failure in failures)
    return failures[0]


def _is_probe(body):
    # Generated data can reuse a marker as `name`, so skip that field.
    if not isinstance(body, dict):
        return False
    return any(
        key != "name"
        and (
            (key in PRIVILEGE_FIELDS and value == PRIVILEGE_FIELDS[key])
            or (isinstance(value, str) and value.startswith("st-ma-"))
            or (value == MARKER_NUMBER and not isinstance(value, bool))
        )
        for key, value in body.items()
    )


def _probes(app):
    return [entry for entry in app.config["requests"] if _is_probe(entry[2])]


# Core behavior


def test_stored_protected_fields_fail_on_create():
    app = _users_app(_schema(), mode="vulnerable")
    failure = _the_failure(_run(app))
    assert failure.code == "ST403"
    assert set(failure.fields) >= {"role", "is_admin"}
    assert "GET /users/" in failure.message
    assert failure.read_case_id is not None


def test_stored_protected_fields_fail_on_update():
    app = _users_app(_schema(), mode="vulnerable")
    with app.test_client() as client:
        client.post("/users", json={"name": "alice"})
    event = _run(app, operation="PUT /users/{user_id}", parameters={"user_id": 1})
    assert set(_the_failure(event).fields) >= {"role", "is_admin"}


def test_partial_update_with_patch():
    app = _users_app(_schema(item_methods=("get", "patch")), mode="vulnerable")
    with app.test_client() as client:
        client.post("/users", json={"name": "alice"})
    event = _run(app, operation="PATCH /users/{user_id}", parameters={"user_id": 1})
    assert "role" in _the_failure(event).fields


@pytest.mark.parametrize("mode", ["secure", "strict"])
def test_ignored_or_rejected_fields_pass(mode):
    app = _users_app(_schema(), mode=mode)
    assert _run(app).status == Status.SUCCESS


def test_echoing_api_does_not_fail():
    # Returns the injected values but stores nothing.
    app = _users_app(_schema(), mode="echo")
    assert _run(app).status == Status.SUCCESS


def test_probe_resources_are_deleted():
    app = _users_app(_schema(), mode="vulnerable")
    _run(app)
    assert app.config["deleted"]


def test_without_a_read_operation_the_write_response_needs_a_baseline():
    # No GET, so the write response is compared with the original response.
    app = _users_app(_schema(item_methods=("put",)), mode="vulnerable")
    failure = _the_failure(_run(app))
    assert "the write response" in failure.message
    assert failure.read_case_id is None


def test_undocumented_privilege_fields():
    spec = _schema(request_schema=USER_INPUT, response_schema=USER_INPUT)
    app = _users_app(spec, mode="vulnerable")
    failure = _the_failure(_run(app))
    # Booleans need a baseline, but `balance` has a unique value.
    assert "balance" in failure.fields
    assert "undocumented" in failure.message


def test_disabled_by_default():
    assert ChecksConfig().mass_assignment.enabled is False
    assert ChecksConfig.from_dict({}).mass_assignment.enabled is False
    assert ChecksConfig.from_dict({"mass_assignment": {"enabled": True}}).mass_assignment.enabled is True
    config = ChecksConfig()
    config.update(included_check_names=["all"])
    assert config.mass_assignment.enabled is True


def test_not_run_unless_enabled():
    app = _users_app(_schema(), mode="vulnerable")
    assert _run(app, checks=[]).status == Status.SUCCESS
    assert not _probes(app)


def test_probes_are_capped_per_operation():
    app = _users_app(_schema(), mode="strict")
    _run(app, max_examples=50)
    assert len(_probes(app)) == 3


def test_negative_cases_are_not_probed():
    app = _users_app(_schema(), mode="vulnerable")
    _run(app, modes=[GenerationMode.NEGATIVE], max_examples=10)
    assert not _probes(app)


def test_probes_keep_credentials():
    header = ("Authorization", "Bearer secret")
    app = _users_app(_schema(), mode="vulnerable", require_header=header)
    event = _run(app, headers={header[0]: header[1]})
    assert "GET /users/" in _the_failure(event).message


# Irregular APIs


def test_booleans_stored_as_integers():
    def encode(record):
        return {key: int(value) if isinstance(value, bool) else value for key, value in record.items()}

    spec = _schema(response_schema={**USER_SCHEMA, "properties": {**USER_SCHEMA["properties"], "is_admin": {}}})
    app = _users_app(spec, mode="vulnerable", encode=encode)
    assert "is_admin" in _the_failure(_run(app)).fields


def test_numbers_returned_as_strings():
    spec = _schema(request_schema=USER_INPUT, response_schema=USER_INPUT)

    def encode(record):
        return {key: str(value) if key == "balance" else value for key, value in record.items()}

    app = _users_app(spec, mode="vulnerable", encode=encode)
    assert "balance" in _the_failure(_run(app)).fields


@pytest.mark.parametrize("created_body", [True, False])
def test_relative_location_header_with_string_ids(created_body):
    app = _users_app(
        _schema(item_parameter_type="string"),
        mode="vulnerable",
        location=True,
        string_ids=True,
        created_body=created_body,
    )
    failure = _the_failure(_run(app))
    assert "GET /users/u" in failure.message


def test_lagging_read_falls_back_to_the_write_response():
    app = _users_app(_schema(), mode="vulnerable", read_misses=10_000)
    failure = _the_failure(_run(app))
    assert "the write response" in failure.message


def test_unique_fields_are_changed_so_the_probe_is_not_a_duplicate():
    spec = _schema(
        request_schema={
            **USER_SCHEMA,
            "properties": {**USER_SCHEMA["properties"], "email": {"type": "string", "format": "email"}},
        },
    )
    app = _users_app(spec, mode="vulnerable", unique="name")
    assert "role" in _the_failure(_run(app)).fields


@pytest.mark.parametrize("status", [403, 429, 500, 503])
def test_blocked_probes_pass(status):
    app = _users_app(_schema(), mode="vulnerable", probe_status=status)
    event = _run(app)
    assert not [failure for failure in _failures(event) if isinstance(failure, MassAssignment)]


def test_failed_cleanup_still_reports():
    app = _users_app(_schema(), mode="vulnerable", delete_status=500)
    _the_failure(_run(app))
    assert app.config["deleted"]


def test_swagger_2():
    spec = {
        "swagger": "2.0",
        "info": {"title": "Users", "version": "1"},
        "basePath": "/",
        "paths": {
            "/users": {
                "post": {
                    "consumes": ["application/json"],
                    "produces": ["application/json"],
                    "parameters": [{"in": "body", "name": "body", "required": True, "schema": USER_SCHEMA}],
                    "responses": {"201": {"description": "OK", "schema": USER_SCHEMA}},
                }
            },
            "/users/{user_id}": {
                "get": {
                    "produces": ["application/json"],
                    "parameters": [{"name": "user_id", "in": "path", "required": True, "type": "integer"}],
                    "responses": {"200": {"description": "OK", "schema": USER_SCHEMA}},
                }
            },
        },
    }
    app = _users_app(spec, mode="vulnerable")
    assert {"role", "is_admin"} <= set(_the_failure(_run(app)).fields)


def test_composed_schema_with_all_of():
    composed = {
        "allOf": [
            {
                "type": "object",
                "properties": {"name": {"type": "string", "minLength": 1, "maxLength": 20}},
                "required": ["name"],
            },
            {"type": "object", "properties": {"role": {"type": "string", "enum": ["user", "admin"], "readOnly": True}}},
        ]
    }
    app = _users_app(_schema(request_schema=composed, response_schema=composed), mode="vulnerable")
    assert "role" in _the_failure(_run(app)).fields


def test_normalized_timestamps():
    approved = {"type": "string", "format": "date-time", "readOnly": True}
    schema = {**USER_SCHEMA, "properties": {**USER_SCHEMA["properties"], "approved_at": approved}}

    def encode(record):
        value = record.get("approved_at")
        if isinstance(value, str) and value.endswith("Z"):
            record = {**record, "approved_at": value[:-1] + ".000+00:00"}
        return record

    app = _users_app(_schema(request_schema=schema, response_schema=schema), mode="vulnerable", encode=encode)
    assert "approved_at" in _the_failure(_run(app)).fields


def test_array_bodies_are_not_probed():
    spec = _schema(request_schema={"type": "array", "items": {"type": "string"}, "maxItems": 2})
    app = _users_app(spec, mode="vulnerable")

    @app.before_request
    def accept_arrays():
        if isinstance(request.get_json(silent=True), list):
            return jsonify({"id": 1, **DEFAULTS}), 201
        return None

    assert _run(app).status == Status.SUCCESS
    assert not _probes(app)


# Configuration


def test_configured_fields():
    app = _users_app(_schema(), mode="vulnerable")
    failure = _the_failure(_run(app, config={"fields": {"plan": "enterprise", "owner_id": 4242}}))
    assert {"plan", "owner_id"} <= set(failure.fields)
    assert "configured" in failure.message


def test_excluded_fields():
    spec = _schema(request_schema=USER_SCHEMA, response_schema=USER_SCHEMA)
    app = _users_app(spec, mode="vulnerable")
    config = {"exclude-fields": ["role", "is_admin"], "privilege-fields": False}
    assert _run(app, config=config).status == Status.SUCCESS


def test_privilege_fields_can_be_turned_off():
    spec = _schema(request_schema=USER_INPUT, response_schema=USER_INPUT)
    app = _users_app(spec, mode="vulnerable")
    assert _run(app, config={"privilege-fields": False}).status == Status.SUCCESS
    assert not _probes(app)


def test_max_attempts():
    app = _users_app(_schema(), mode="strict")
    _run(app, max_examples=50, config={"max-attempts": 1})
    assert len(_probes(app)) == 1


def test_cleanup_can_be_turned_off():
    app = _users_app(_schema(), mode="vulnerable")
    _the_failure(_run(app, config={"cleanup": False}))
    assert not app.config["deleted"]


def _nested_app(*, vulnerable):
    """JSON:API-style API read back at a custom path."""
    attributes = {
        "type": "object",
        "properties": {
            "name": {"type": "string", "minLength": 1, "maxLength": 20},
            "tier": {"type": "string", "enum": ["free", "enterprise"], "readOnly": True},
        },
        "required": ["name"],
    }
    document = {
        "type": "object",
        "properties": {
            "data": {
                "type": "object",
                "properties": {"uuid": {"type": "string", "readOnly": True}, "attributes": attributes},
                "required": ["attributes"],
            }
        },
        "required": ["data"],
    }
    response = {"description": "OK", "content": {"application/vnd.api+json": {"schema": document}}}
    spec = {
        "openapi": "3.0.3",
        "info": {"title": "Accounts", "version": "1"},
        "paths": {
            "/accounts": {
                "post": {
                    "requestBody": {"required": True, "content": {"application/json": {"schema": document}}},
                    "responses": {"201": response},
                }
            },
            "/lookup/accounts/{uuid}": {
                "get": {
                    "parameters": [{"name": "uuid", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "responses": {"200": response},
                }
            },
        },
    }
    app = make_flask_app_from_schema(spec)
    accounts = {}
    ids = itertools.count(1)

    @app.route("/accounts", methods=["POST"])
    def create():
        payload = request.get_json()
        attrs = (payload.get("data") or {}).get("attributes") or {}
        uuid = f"acc-{next(ids)}"
        stored = {"name": attrs.get("name"), "tier": "free"}
        if vulnerable:
            stored.update(attrs)
        accounts[uuid] = stored
        response = jsonify({"data": {"uuid": uuid, "attributes": stored}})
        response.status_code = 201
        response.content_type = "application/vnd.api+json"
        return response

    @app.route("/lookup/accounts/<uuid>", methods=["GET"])
    def lookup(uuid):
        response = jsonify({"data": {"uuid": uuid, "attributes": accounts[uuid]}})
        response.content_type = "application/vnd.api+json"
        return response

    return app


NESTED_CONFIG = {
    "request-envelope": "data.attributes",
    "response-envelope": "data.attributes",
    "read": "GET /lookup/accounts/{uuid}",
    "id-field": "data.uuid",
}


@pytest.mark.parametrize(("vulnerable", "expected"), [(True, Status.FAILURE), (False, Status.SUCCESS)])
def test_nested_envelopes_and_custom_read(vulnerable, expected):
    app = _nested_app(vulnerable=vulnerable)
    event = _run(app, operation="POST /accounts", config=NESTED_CONFIG)
    assert event.status == expected
    if vulnerable:
        failure = _the_failure(event)
        assert "tier" in failure.fields
        assert "GET /lookup/accounts/acc-" in failure.message


def test_nested_envelopes_need_configuration():
    # Without config the check finds nothing here, but must not fail.
    app = _nested_app(vulnerable=True)
    assert _run(app, operation="POST /accounts").status == Status.SUCCESS


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ({"max-attempts": 0}, "must be at least 1"),
        ({"read": "POST /users"}, "must name a `GET` operation"),
        ({"read": "GET"}, "must name a `GET` operation"),
    ],
)
def test_invalid_configuration(data, message):
    with pytest.raises(ConfigError, match=message):
        MassAssignmentConfig.from_dict(data)


def test_configuration_from_toml_keys():
    config = MassAssignmentConfig.from_dict(
        {
            "enabled": True,
            "fields": {"plan": "pro"},
            "exclude-fields": ["role"],
            "privilege-fields": False,
            "request-envelope": "data",
            "response-envelope": "",
            "read": "GET /users/{id}",
            "id-field": "id",
            "max-attempts": 5,
            "cleanup": False,
        }
    )
    assert config.enabled
    assert config.fields == {"plan": "pro"}
    assert config.exclude_fields == ["role"]
    assert not config.privilege_fields
    assert config.request_envelope == "data"
    assert config.response_envelope == ""
    assert config.read == "GET /users/{id}"
    assert config.id_field == "id"
    assert config.max_attempts == 5
    assert not config.cleanup


def test_per_operation_settings():
    app = _users_app(_schema(), mode="vulnerable")
    config = schemathesis.Config.from_dict(
        {
            "checks": {"mass_assignment": {"enabled": True}},
            "operations": [
                {"include-name": "POST /users", "checks": {"mass_assignment": {"exclude-fields": ["role"]}}}
            ],
        }
    )
    schema = schemathesis.openapi.from_wsgi("/openapi.json", app=app, config=config).include(name="POST /users")
    failure = _the_failure(_run(app, schema=schema))
    assert "role" not in failure.fields
    assert "is_admin" in failure.fields


# Hooks


def _hooked_schema(app, hook):
    schema = _schema_for(app)
    schema.hook("before_mass_assignment_probe")(hook)
    return schema


def test_hook_adds_and_removes_fields():
    app = _users_app(_schema(), mode="vulnerable")
    seen = []

    def hook(context, probe):
        assert isinstance(probe, MassAssignmentProbe)
        assert probe.original.operation.label == "POST /users"
        seen.append(dict(probe.fields))
        probe.fields.pop("role", None)
        probe.fields["plan"] = "enterprise"

    failure = _the_failure(_run(app, schema=_hooked_schema(app, hook)))
    assert seen and "role" in seen[0]
    assert "role" not in failure.fields
    assert "plan" in failure.fields
    assert "hook" in failure.message


def test_hook_skips_probe():
    app = _users_app(_schema(), mode="vulnerable")

    def hook(context, probe):
        probe.skip = True

    assert _run(app, schema=_hooked_schema(app, hook)).status == Status.SUCCESS
    assert not _probes(app)


def test_hook_adds_header_the_api_requires():
    app = _users_app(_schema(), mode="vulnerable")

    @app.before_request
    def csrf():
        payload = request.get_json(silent=True)
        is_probe = isinstance(payload, dict) and payload.get("balance") == MARKER_NUMBER
        if is_probe and request.headers.get("X-CSRF-Token") != "t0k3n":
            return jsonify({"detail": "csrf"}), 403
        return None

    assert _run(app).status == Status.SUCCESS

    app = _users_app(_schema(), mode="vulnerable")
    app.before_request(csrf)

    def hook(context, probe):
        probe.case.headers["X-CSRF-Token"] = "t0k3n"

    _the_failure(_run(app, schema=_hooked_schema(app, hook)))


def test_global_hook():
    app = _users_app(_schema(), mode="vulnerable")
    calls = []

    @schemathesis.hook("before_mass_assignment_probe")
    def record(context, probe):
        calls.append(context.operation.label)

    try:
        _run(app)
    finally:
        schemathesis.hooks.unregister(record)
    assert calls == ["POST /users"]


# Units


@pytest.mark.parametrize(
    ("schema", "baseline", "expected", "unique"),
    [
        ({"type": "boolean"}, False, True, False),
        ({"type": "boolean"}, True, False, False),
        ({"type": "boolean"}, _MISSING, True, False),
        ({"type": "string", "enum": ["user", "admin"]}, "user", "admin", False),
        ({"type": "string", "enum": ["user", "editor"]}, "user", "editor", False),
        ({"type": "string", "enum": ["user"]}, "user", _MISSING, False),
        ({"type": "integer"}, 0, MARKER_NUMBER, True),
        ({"type": "integer"}, MARKER_NUMBER, MARKER_NUMBER + 1, True),
        ({"type": "integer", "maximum": 100}, 0, _MISSING, False),
        ({"type": ["integer", "null"]}, None, MARKER_NUMBER, True),
        ({"type": "string", "format": "date-time"}, _MISSING, MARKER_DATE_TIME, True),
        ({"type": "string", "pattern": "^[a-z]+$"}, _MISSING, _MISSING, False),
        ({"type": "string", "maxLength": 3}, _MISSING, _MISSING, False),
        ({"type": "string", "minLength": 100}, _MISSING, _MISSING, False),
        ({"type": "object"}, _MISSING, _MISSING, False),
        ({"type": "array"}, _MISSING, _MISSING, False),
        ({}, _MISSING, _MISSING, False),
        ({"const": "x"}, _MISSING, _MISSING, False),
    ],
)
def test_choose_value(schema, baseline, expected, unique):
    target = Target(name="field", origin=FieldOrigin.READ_ONLY, schema=schema, baseline=baseline)
    assert choose_value(target, "abcd1234") == (expected, unique)


@pytest.mark.parametrize(
    ("format", "expected"),
    [
        (None, "st-ma-abcd1234"),
        ("email", "st-ma-abcd1234@example.com"),
        ("uri", "https://example.com/st-ma-abcd1234"),
        ("uuid", "abcd1234-0000-0000-0000-000000000000"),
        ("date", "2001-02-03"),
    ],
)
def test_choose_value_for_strings_is_unique(format, expected):
    schema = {"type": "string"} if format is None else {"type": "string", "format": format}
    target = Target(name="field", origin=FieldOrigin.READ_ONLY, schema=schema)
    assert choose_value(target, "abcd1234") == (expected, True)


def test_choose_value_undocumented_skips_when_already_privileged():
    target = Target(name="is_admin", origin=FieldOrigin.UNDOCUMENTED, schema=None, baseline=True)
    assert choose_value(target, "abcd1234") == (_MISSING, False)


@pytest.mark.parametrize(
    ("observed", "expected", "result"),
    [
        (True, True, True),
        (1, True, True),
        ("true", True, True),
        (0, False, True),
        ("false", False, True),
        (0, True, False),
        (2, True, False),
        (None, False, False),
        ("2001-02-03T04:05:06.000Z", MARKER_DATE_TIME, True),
        ("2001-02-03T04:05:06+00:00", MARKER_DATE_TIME, True),
        ("ST-MA-X@EXAMPLE.COM", "st-ma-x@example.com", True),
        (987654321.0, 987654321, True),
        ("987654321", 987654321, True),
        ("not a number", 987654321, False),
        (["admin"], ["admin"], True),
        ("user", "admin", False),
        (True, "admin", False),
    ],
)
def test_matches(observed, expected, result):
    assert matches(observed, expected) is result


def _target(**kwargs):
    defaults = {"name": "role", "origin": FieldOrigin.READ_ONLY, "schema": {}, "value": "admin"}
    return Target(**{**defaults, **kwargs})


def test_evaluate_requires_baseline_or_unique_value_for_reads():
    unknown = _target(baseline=_MISSING)
    assert not list(evaluate([unknown], probe_body=None, read_body={"role": "admin"}, read_label="GET /u/1"))
    known = _target(baseline="user")
    (finding,) = evaluate([known], probe_body=None, read_body={"role": "admin"}, read_label="GET /u/1")
    assert finding.observed_with == "GET /u/1"
    unique = _target(name="note", value="st-ma-1", unique=True)
    assert list(evaluate([unique], probe_body=None, read_body={"note": "st-ma-1"}, read_label="GET /u/1"))


def test_evaluate_write_response_alone_needs_a_baseline():
    unique = _target(name="note", value="st-ma-1", unique=True)
    assert not list(evaluate([unique], probe_body={"note": "st-ma-1"}, read_body=None, read_label=None))
    known = _target(baseline="user")
    (finding,) = evaluate([known], probe_body={"role": "admin"}, read_body=None, read_label=None)
    assert finding.observed_with == "the write response"


def test_evaluate_read_decides_over_write_response():
    known = _target(baseline="user")
    assert not list(evaluate([known], probe_body={"role": "admin"}, read_body={"role": "user"}, read_label="GET /u/1"))


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("id", True),
        ("_id", True),
        ("user_id", True),
        ("ownerId", True),
        ("accountID", True),
        ("uuid", True),
        ("role", False),
        ("paid", False),
        ("valid", False),
    ],
)
def test_is_identifier(name, expected):
    class FakeCase:
        path_parameters = {}

    assert is_identifier(name, FakeCase()) is expected


def test_uniquify():
    properties = {
        "email": {"type": "string", "format": "email"},
        "name": {"type": "string"},
        "kind": {"type": "string", "enum": ["a"]},
        "short": {"type": "string", "maxLength": 3},
        "code": {"type": "string", "maxLength": 4},
    }
    body = {"email": "a@b.co", "name": "x", "kind": "a", "short": "abc", "code": "abcd", "other": "v"}
    result = uniquify(body, properties, "t0k3n")
    assert result == {
        "email": "a+t0k3n@b.co",
        "name": "x-t0k3n",
        "kind": "a",
        "short": "abc",
        "code": "abcd",
        "other": "v",
    }


@pytest.mark.parametrize(
    ("path", "expected"), [(None, ()), ("", ()), ("data", ("data",)), ("data.attributes", ("data", "attributes"))]
)
def test_split_path(path, expected):
    assert split_path(path) == expected


def test_dig():
    body = {"data": {"attributes": {"name": "x"}}, "list": [1]}
    assert dig(body, ("data", "attributes")) == {"name": "x"}
    assert dig(body, ()) is body
    assert dig(body, ("missing",)) is None
    assert dig(body, ("list", "0")) is None
    assert dig(None, ("data",)) is None


ARTICLE = {
    "type": "object",
    "properties": {
        "id": {"type": "string"},
        "title": {"type": "string", "minLength": 1, "maxLength": 20},
        "likes": {"type": "integer"},
        "date": {"type": "string", "format": "date"},
    },
}
ARTICLE_INPUT = {
    "type": "object",
    "properties": {"title": {"type": "string", "minLength": 1, "maxLength": 20}},
    "required": ["title"],
    "additionalProperties": False,
}


def _articles_app(*, vulnerable):
    # Enveloped responses and no GET.
    envelope = {
        "type": "object",
        "properties": {"article": ARTICLE},
        "required": ["article"],
    }
    spec = {
        "openapi": "3.0.3",
        "info": {"title": "Articles", "version": "1"},
        "paths": {
            "/articles/{article_id}": {
                "put": {
                    "parameters": [
                        {"name": "article_id", "in": "path", "required": True, "schema": {"type": "string"}}
                    ],
                    "requestBody": {"required": True, "content": {"application/json": {"schema": ARTICLE_INPUT}}},
                    "responses": {"200": {"description": "OK", "content": {"application/json": {"schema": envelope}}}},
                }
            }
        },
    }
    app = make_flask_app_from_schema(spec)
    articles: dict[str, dict] = {}

    @app.route("/articles/<article_id>", methods=["PUT"])
    def put(article_id):
        payload = request.get_json()
        article = articles.get(article_id, {"id": article_id, "likes": 0, "date": "2026-11-15"})
        for key, value in payload.items():
            if vulnerable or key == "title":
                article[key] = value
        articles[article_id] = article
        return jsonify({"article": article})

    return app


@pytest.mark.parametrize(("vulnerable", "expected"), [(True, Status.FAILURE), (False, Status.SUCCESS)])
def test_enveloped_responses(vulnerable, expected):
    app = _articles_app(vulnerable=vulnerable)
    event = _run(app, operation="PUT /articles/{article_id}")
    assert event.status == expected
    if vulnerable:
        failure = _the_failure(event)
        assert {"likes", "date"} <= set(failure.fields)
        assert "the write response" in failure.message
