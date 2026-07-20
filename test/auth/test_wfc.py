from __future__ import annotations

import json
import os

import pytest
from fastapi import FastAPI
from flask import Flask

import schemathesis
from schemathesis.auths import AuthContext
from schemathesis.wfc.converter import wfc_to_auth_provider
from schemathesis.wfc.errors import WFCLoginError
from schemathesis.wfc.loader import load_from_dict
from test.apps.catalog.openapi import wfc as wfc_apps
from test.apps.catalog.openapi.wfc import WFC_PASSWORD, WFC_SESSION, WFC_TOKEN, WFC_USERNAME

CREDENTIALS = {
    "username": WFC_USERNAME,
    "password": WFC_PASSWORD,
    "usernameField": "username",
    "passwordField": "password",
}


def _write(tmp_path, doc, *, name="auth.json"):
    path = tmp_path / name
    path.write_text(json.dumps(doc) if name.endswith(".json") else doc)
    return str(path)


def _token(**overrides):
    return {
        "extractFrom": "body",
        "extractSelector": "/access_token",
        "sendIn": "header",
        "sendName": "Authorization",
        "sendTemplate": "Bearer {token}",
        **overrides,
    }


def _login(*, token=None, **overrides):
    block = {"verb": "POST", "endpoint": "/api/login", "contentType": "application/json", "payloadUserPwd": CREDENTIALS}
    if token is not None:
        block["token"] = token
    block.update(overrides)
    return block


def _header(call, name):
    return next((v for k, v in call.headers.items() if k.lower() == name.lower()), None)


def _protected_calls(api):
    calls = api.calls_to("/api/protected")
    assert calls, "engine never called the protected endpoint"
    return calls


def _run_wfc(cli, api, path, *args, max_examples=5, **wfc):
    return cli.run(
        api.schema_url,
        f"--max-examples={max_examples}",
        *args,
        config={"auth": {"wfc": {"path": path, **wfc}}},
    )


def test_wfc_fixed_headers_reach_requests(cli, ctx, tmp_path):
    api = ctx.openapi.apps.wfc_login()
    auth = _write(tmp_path, {"auth": [{"name": "u", "fixedHeaders": [{"name": "X-Api-Key", "value": "static"}]}]})

    assert _run_wfc(cli, api, auth).exit_code == 0
    assert all(_header(c, "X-Api-Key") == "static" for c in _protected_calls(api))


def test_wfc_body_token_reaches_requests(cli, ctx, tmp_path):
    api = ctx.openapi.apps.wfc_login()
    auth = _write(tmp_path, {"auth": [{"name": "u", "loginEndpointAuth": _login(token=_token())}]})

    assert _run_wfc(cli, api, auth).exit_code == 0
    assert all(_header(c, "Authorization") == f"Bearer {WFC_TOKEN}" for c in _protected_calls(api))


def test_wfc_header_token_reaches_requests(cli, ctx, tmp_path):
    api = ctx.openapi.apps.wfc_login()
    token = _token(extractFrom="header", extractSelector="X-Auth-Token")
    auth = _write(tmp_path, {"auth": [{"name": "u", "loginEndpointAuth": _login(token=token)}]})

    assert _run_wfc(cli, api, auth).exit_code == 0
    assert all(_header(c, "Authorization") == f"Bearer {WFC_TOKEN}" for c in _protected_calls(api))


def test_wfc_query_token_reaches_requests(cli, ctx, tmp_path):
    api = ctx.openapi.apps.wfc_login()
    token = _token(sendIn="query", sendName="token", sendTemplate="{token}")
    auth = _write(tmp_path, {"auth": [{"name": "u", "loginEndpointAuth": _login(token=token)}]})

    assert _run_wfc(cli, api, auth).exit_code == 0
    assert all(c.query.get("token") == WFC_TOKEN for c in _protected_calls(api))


def test_wfc_number_token_coerced(cli, ctx, tmp_path):
    api = ctx.openapi.apps.wfc_login()
    token = _token(extractSelector="/number_token", sendName="X-Token", sendTemplate="{token}")
    auth = _write(tmp_path, {"auth": [{"name": "u", "loginEndpointAuth": _login(token=token)}]})

    assert _run_wfc(cli, api, auth).exit_code == 0
    assert all(_header(c, "X-Token") == "42" for c in _protected_calls(api))


def test_wfc_cookie_auth_reaches_requests(cli, ctx, tmp_path):
    api = ctx.openapi.apps.wfc_login()
    auth = _write(tmp_path, {"auth": [{"name": "u", "loginEndpointAuth": _login(expectCookies=True)}]})

    assert _run_wfc(cli, api, auth).exit_code == 0
    assert all(f"session={WFC_SESSION}" in (_header(c, "Cookie") or "") for c in _protected_calls(api))


def test_wfc_form_encoded_login(cli, ctx, tmp_path):
    api = ctx.openapi.apps.wfc_login()
    login = _login(token=_token(), contentType="application/x-www-form-urlencoded")
    auth = _write(tmp_path, {"auth": [{"name": "u", "loginEndpointAuth": login}]})

    assert _run_wfc(cli, api, auth).exit_code == 0
    assert all(_header(c, "Authorization") == f"Bearer {WFC_TOKEN}" for c in _protected_calls(api))


def test_wfc_payload_raw_login(cli, ctx, tmp_path):
    api = ctx.openapi.apps.wfc_login()
    raw = json.dumps({"username": WFC_USERNAME, "password": WFC_PASSWORD})
    login = {
        "verb": "POST",
        "endpoint": "/api/login",
        "contentType": "application/json",
        "payloadRaw": raw,
        "token": _token(),
    }
    auth = _write(tmp_path, {"auth": [{"name": "u", "loginEndpointAuth": login}]})

    assert _run_wfc(cli, api, auth).exit_code == 0
    assert all(_header(c, "Authorization") == f"Bearer {WFC_TOKEN}" for c in _protected_calls(api))


def test_wfc_external_endpoint_url(cli, ctx, tmp_path):
    api = ctx.openapi.apps.wfc_login()
    login = {
        "verb": "POST",
        "externalEndpointURL": f"{api.base_url}/api/login",
        "contentType": "application/json",
        "payloadUserPwd": CREDENTIALS,
        "token": _token(),
    }
    auth = _write(tmp_path, {"auth": [{"name": "u", "loginEndpointAuth": login}]})

    assert _run_wfc(cli, api, auth).exit_code == 0
    assert all(_header(c, "Authorization") == f"Bearer {WFC_TOKEN}" for c in _protected_calls(api))


def test_wfc_selects_named_user(cli, ctx, tmp_path):
    api = ctx.openapi.apps.wfc_login()
    auth = _write(
        tmp_path,
        {
            "auth": [
                {"name": "alice", "fixedHeaders": [{"name": "X-Api-Key", "value": "alice-key"}]},
                {"name": "bob", "fixedHeaders": [{"name": "X-Api-Key", "value": "bob-key"}]},
            ]
        },
    )

    assert _run_wfc(cli, api, auth, user="bob").exit_code == 0
    assert all(_header(c, "X-Api-Key") == "bob-key" for c in _protected_calls(api))


def test_wfc_yaml_document(cli, ctx, tmp_path):
    api = ctx.openapi.apps.wfc_login()
    doc = "auth:\n  - name: u\n    fixedHeaders:\n      - name: X-Api-Key\n        value: static\n"
    auth = _write(tmp_path, doc, name="auth.yaml")

    assert _run_wfc(cli, api, auth).exit_code == 0
    assert all(_header(c, "X-Api-Key") == "static" for c in _protected_calls(api))


@pytest.mark.parametrize(
    ("selector", "match"),
    [
        ("/missing", "not found at json pointer"),
        ("/null_token", "is null"),
        ("/object_token", "not a string or primitive"),
    ],
    ids=["unresolvable", "null", "wrong-type"],
)
def test_wfc_body_token_extraction_errors(cli, ctx, tmp_path, selector, match):
    api = ctx.openapi.apps.wfc_login()
    login = _login(token=_token(extractSelector=selector))
    auth = _write(tmp_path, {"auth": [{"name": "u", "loginEndpointAuth": login}]})

    result = _run_wfc(cli, api, auth, max_examples=1)
    assert result.exit_code != 0
    assert match in result.stdout.lower()


def test_wfc_header_token_missing(cli, ctx, tmp_path):
    api = ctx.openapi.apps.wfc_login()
    login = _login(token=_token(extractFrom="header", extractSelector="X-Absent"))
    auth = _write(tmp_path, {"auth": [{"name": "u", "loginEndpointAuth": login}]})

    result = _run_wfc(cli, api, auth, max_examples=1)
    assert result.exit_code != 0
    assert "header 'x-absent' not found" in result.stdout.lower()


def test_wfc_login_custom_headers(cli, ctx, tmp_path):
    api = ctx.openapi.apps.wfc_login()
    login = _login(token=_token(), headers=[{"name": "X-Login", "value": "on"}])
    auth = _write(tmp_path, {"auth": [{"name": "u", "loginEndpointAuth": login}]})

    assert _run_wfc(cli, api, auth).exit_code == 0
    assert all(_header(c, "Authorization") == f"Bearer {WFC_TOKEN}" for c in _protected_calls(api))


def test_wfc_login_non_2xx(cli, ctx, tmp_path):
    api = ctx.openapi.apps.wfc_login_failing()
    auth = _write(tmp_path, {"auth": [{"name": "u", "loginEndpointAuth": _login(token=_token())}]})

    result = _run_wfc(cli, api, auth, max_examples=1)
    assert result.exit_code != 0
    assert "500" in result.stdout


def test_wfc_login_body_not_json(cli, ctx, tmp_path):
    api = ctx.openapi.apps.wfc_login_plain()
    auth = _write(tmp_path, {"auth": [{"name": "u", "loginEndpointAuth": _login(token=_token())}]})

    result = _run_wfc(cli, api, auth, max_examples=1)
    assert result.exit_code != 0
    assert "not valid json" in result.stdout.lower()


def test_wfc_login_expects_cookies_but_none_returned(cli, ctx, tmp_path):
    api = ctx.openapi.apps.wfc_login_plain()
    auth = _write(tmp_path, {"auth": [{"name": "u", "loginEndpointAuth": _login(expectCookies=True)}]})

    result = _run_wfc(cli, api, auth, max_examples=1)
    assert result.exit_code != 0
    assert "no cookies returned" in result.stdout.lower()


def test_wfc_unsupported_content_type(cli, ctx, tmp_path):
    api = ctx.openapi.apps.wfc_login()
    login = _login(token=_token(), contentType="application/xml")
    auth = _write(tmp_path, {"auth": [{"name": "u", "loginEndpointAuth": login}]})

    result = _run_wfc(cli, api, auth, max_examples=1)
    assert result.exit_code != 0
    assert "unsupported content type" in result.stdout.lower()


def test_wfc_login_forwards_client_cert(cli, ctx, tmp_path):
    api = ctx.openapi.apps.wfc_login()
    cert = tmp_path / "client.pem"
    cert.write_text("dummy")
    auth = _write(tmp_path, {"auth": [{"name": "u", "loginEndpointAuth": _login(token=_token())}]})

    assert _run_wfc(cli, api, auth, f"--request-cert={cert}").exit_code == 0
    assert all(_header(c, "Authorization") == f"Bearer {WFC_TOKEN}" for c in _protected_calls(api))


def _provider_for(login):
    return wfc_to_auth_provider(load_from_dict({"auth": [{"name": "u", "loginEndpointAuth": login}]})[0])


def test_wfc_wsgi_login_applies_token():
    app = wfc_apps.wfc_login().server
    operation = schemathesis.openapi.from_wsgi("/openapi.json", app)["/api/protected"]["GET"]
    provider = _provider_for(_login(token=_token()))
    context = AuthContext(operation=operation, app=app)
    case = operation.Case()
    provider.set(case, provider.get(case, context), context)
    assert case.headers["Authorization"] == f"Bearer {WFC_TOKEN}"


def test_wfc_asgi_login_applies_token():
    app = FastAPI()

    @app.post("/api/login")
    def login(payload: dict) -> dict:
        return {"access_token": WFC_TOKEN}

    @app.get("/api/protected")
    def protected() -> dict:
        return {"ok": True}

    operation = schemathesis.openapi.from_asgi("/openapi.json", app)["/api/protected"]["GET"]
    provider = _provider_for(_login(token=_token()))
    context = AuthContext(operation=operation, app=app)
    case = operation.Case()
    provider.set(case, provider.get(case, context), context)
    assert case.headers["Authorization"] == f"Bearer {WFC_TOKEN}"


@pytest.mark.parametrize("charset", ["bogus-xyz", "undefined"], ids=["unknown-charset", "undefined-codec"])
def test_wfc_http_login_bad_charset(ctx, app_runner, charset):
    # A login endpoint declaring an unknown or broken charset must not crash the login request.
    app, _ = ctx.openapi.make_flask_app({"/api/protected": {"get": {"responses": {"200": {"description": "OK"}}}}})

    @app.route("/api/login", methods=["POST"])
    def login() -> tuple[str, int, dict]:
        return json.dumps({"access_token": WFC_TOKEN}), 200, {"Content-Type": f"application/json; charset={charset}"}

    operation = schemathesis.openapi.from_url(app_runner.openapi_url(app))["/api/protected"]["GET"]
    provider = _provider_for(_login(token=_token()))
    context = AuthContext(operation=operation, app=None)
    case = operation.Case()
    provider.set(case, provider.get(case, context), context)
    assert case.headers["Authorization"] == f"Bearer {WFC_TOKEN}"


def test_wfc_http_login_bom_json(ctx, app_runner):
    # UTF-8-BOM JSON login responses (common for .NET services) must still yield the token.
    app, _ = ctx.openapi.make_flask_app({"/api/protected": {"get": {"responses": {"200": {"description": "OK"}}}}})

    @app.route("/api/login", methods=["POST"])
    def login() -> tuple[bytes, int, dict]:
        body = b"\xef\xbb\xbf" + json.dumps({"access_token": WFC_TOKEN}).encode("utf-8")
        return body, 200, {"Content-Type": "application/json"}

    operation = schemathesis.openapi.from_url(app_runner.openapi_url(app))["/api/protected"]["GET"]
    provider = _provider_for(_login(token=_token()))
    context = AuthContext(operation=operation, app=None)
    case = operation.Case()
    provider.set(case, provider.get(case, context), context)
    assert case.headers["Authorization"] == f"Bearer {WFC_TOKEN}"


_SPEC = {
    "openapi": "3.0.0",
    "info": {"title": "t", "version": "1"},
    "paths": {"/api/protected": {"get": {"responses": {"200": {"description": "OK"}}}}},
}


def test_wfc_wsgi_login_transport_error():
    app = Flask("crash")
    app.config["TESTING"] = True

    @app.route("/openapi.json")
    def spec() -> dict:
        return _SPEC

    @app.route("/api/login", methods=["POST"])
    def login() -> dict:
        raise RuntimeError("boom")

    operation = schemathesis.openapi.from_wsgi("/openapi.json", app)["/api/protected"]["GET"]
    provider = _provider_for(_login(token=_token()))
    context = AuthContext(operation=operation, app=app)
    with pytest.raises(WFCLoginError, match="WSGI login request failed"):
        provider.get(operation.Case(), context)


def test_wfc_asgi_login_transport_error():
    app = FastAPI()

    @app.post("/api/login")
    def login(payload: dict) -> dict:
        raise RuntimeError("boom")

    @app.get("/api/protected")
    def protected() -> dict:
        return {"ok": True}

    operation = schemathesis.openapi.from_asgi("/openapi.json", app)["/api/protected"]["GET"]
    provider = _provider_for(_login(token=_token()))
    context = AuthContext(operation=operation, app=app)
    with pytest.raises(WFCLoginError, match="ASGI login request failed"):
        provider.get(operation.Case(), context)


def test_wfc_unreadable_file(cli, ctx, tmp_path):
    api = ctx.openapi.apps.wfc_login()
    path = tmp_path / "auth.json"
    path.write_text("{}")
    path.chmod(0o000)
    if os.access(path, os.R_OK):
        pytest.skip("filesystem ignored chmod 0 (likely running as root or Windows)")

    result = _run_wfc(cli, api, str(path), max_examples=1)
    assert result.exit_code != 0
    assert "failed to read" in result.stdout.lower()


def test_wfc_login_connection_error(cli, ctx, tmp_path):
    api = ctx.openapi.apps.wfc_login()
    login = {
        "verb": "POST",
        "externalEndpointURL": "http://127.0.0.1:1/api/login",
        "contentType": "application/json",
        "payloadUserPwd": CREDENTIALS,
        "token": _token(),
    }
    auth = _write(tmp_path, {"auth": [{"name": "u", "loginEndpointAuth": login}]})

    result = _run_wfc(cli, api, auth, max_examples=1)
    assert result.exit_code != 0
    assert "login endpoint call failed" in result.stdout.lower()


def test_wfc_multiple_users_without_selection(cli, ctx, tmp_path):
    api = ctx.openapi.apps.wfc_login()
    auth = _write(
        tmp_path,
        {
            "auth": [
                {"name": "a", "fixedHeaders": [{"name": "X", "value": "1"}]},
                {"name": "b", "fixedHeaders": [{"name": "X", "value": "2"}]},
            ]
        },
    )

    result = _run_wfc(cli, api, auth, max_examples=1)
    assert result.exit_code != 0
    assert "specify which user" in result.stdout.lower()


def test_wfc_unknown_user(cli, ctx, tmp_path):
    api = ctx.openapi.apps.wfc_login()
    auth = _write(
        tmp_path,
        {
            "auth": [
                {"name": "a", "fixedHeaders": [{"name": "X", "value": "1"}]},
                {"name": "b", "fixedHeaders": [{"name": "X", "value": "2"}]},
            ]
        },
    )

    result = _run_wfc(cli, api, auth, max_examples=1, user="carol")
    assert result.exit_code != 0
    assert "carol" in result.stdout.lower() and "not found" in result.stdout.lower()


@pytest.mark.parametrize(
    ("filename", "content", "match"),
    [
        ("bad.json", "{not json", "invalid json"),
        ("bad.yaml", "key: [unclosed", "invalid yaml"),
        ("bad.txt", "{}", "unsupported file extension"),
        ("list.json", "[]", "must be an object"),
    ],
    ids=["invalid-json", "invalid-yaml", "bad-extension", "non-object"],
)
def test_wfc_file_errors(cli, ctx, tmp_path, filename, content, match):
    api = ctx.openapi.apps.wfc_login()
    path = tmp_path / filename
    path.write_text(content)

    result = _run_wfc(cli, api, str(path), max_examples=1)
    assert result.exit_code != 0
    assert match in result.stdout.lower()


def test_wfc_file_not_found(cli, ctx, tmp_path):
    api = ctx.openapi.apps.wfc_login()
    result = _run_wfc(cli, api, str(tmp_path / "nope.json"), max_examples=1)
    assert result.exit_code != 0
    assert "not found" in result.stdout.lower()


def test_wfc_path_is_directory(cli, ctx, tmp_path):
    api = ctx.openapi.apps.wfc_login()
    result = _run_wfc(cli, api, str(tmp_path), max_examples=1)
    assert result.exit_code != 0
    assert "not a file" in result.stdout.lower()


@pytest.mark.parametrize(
    ("entries", "match"),
    [
        ([{"name": "u"}], "either 'fixedheaders' or 'loginendpointauth'"),
        (
            [
                {
                    "name": "u",
                    "fixedHeaders": [{"name": "X", "value": "y"}],
                    "loginEndpointAuth": _login(expectCookies=True),
                }
            ],
            "both 'fixedheaders' and 'loginendpointauth'",
        ),
        (
            [
                {
                    "name": "u",
                    "loginEndpointAuth": {
                        "verb": "POST",
                        "endpoint": "/l",
                        "externalEndpointURL": "http://x/l",
                        "expectCookies": True,
                    },
                }
            ],
            "both 'endpoint' and 'externalendpointurl'",
        ),
        (
            [{"name": "u", "loginEndpointAuth": {"verb": "POST", "expectCookies": True}}],
            "either 'endpoint' or 'externalendpointurl'",
        ),
        ([{"name": "u", "loginEndpointAuth": {"verb": "POST", "endpoint": "/l"}}], "either 'token' or 'expectcookies"),
        (
            [{"name": "u", "loginEndpointAuth": _login(token=_token(), expectCookies=True)}],
            "both 'token' and 'expectcookies",
        ),
        (
            [{"name": "u", "loginEndpointAuth": _login(token=_token(extractSelector="no-slash"))}],
            "json pointer",
        ),
        (
            [{"name": "u", "loginEndpointAuth": _login(token=_token(sendTemplate="no-placeholder"))}],
            "must contain '{token}'",
        ),
        (
            [
                {
                    "name": "u",
                    "loginEndpointAuth": {
                        "verb": "POST",
                        "endpoint": "/l",
                        "payloadRaw": "x",
                        "payloadUserPwd": CREDENTIALS,
                        "expectCookies": True,
                    },
                }
            ],
            "both 'payloadraw' and 'payloaduserpwd'",
        ),
        (
            [
                {"name": "d", "fixedHeaders": [{"name": "X", "value": "1"}]},
                {"name": "d", "fixedHeaders": [{"name": "Y", "value": "2"}]},
            ],
            "duplicate auth names",
        ),
        ([{"name": "u", "loginEndpointAuth": {"endpoint": "/l", "expectCookies": True}}], "verb"),
        ([{"name": "u", "loginEndpointAuth": {"verb": "TRACE", "endpoint": "/l", "expectCookies": True}}], "trace"),
    ],
    ids=[
        "no-method",
        "both-methods",
        "endpoint-and-external",
        "neither-endpoint-external",
        "no-token-no-cookies",
        "token-and-cookies",
        "selector-not-pointer",
        "template-no-placeholder",
        "raw-and-userpwd",
        "duplicate-names",
        "missing-verb",
        "bad-verb-enum",
    ],
)
def test_wfc_document_validation_errors(cli, ctx, tmp_path, entries, match):
    api = ctx.openapi.apps.wfc_login()
    auth = _write(tmp_path, {"auth": entries})

    result = _run_wfc(cli, api, auth, max_examples=1)
    assert result.exit_code != 0
    assert match in result.stdout.lower()


def test_wfc_conflicts_with_basic_auth(cli, ctx, tmp_path):
    api = ctx.openapi.apps.wfc_login()
    auth = _write(tmp_path, {"auth": [{"name": "u", "fixedHeaders": [{"name": "X", "value": "k"}]}]})

    result = cli.run(
        api.schema_url,
        "--max-examples=1",
        config={"auth": {"wfc": {"path": auth}, "basic": {"username": "u", "password": "p"}}},
    )
    assert result.exit_code != 0
    assert "multiple authentication methods" in result.stdout.lower()


def test_wfc_login_over_wsgi(testdir):
    testdir.makefile(
        ".json",
        auth=json.dumps(
            {
                "auth": [
                    {
                        "name": "u",
                        "loginEndpointAuth": {
                            "verb": "POST",
                            "endpoint": "/api/login",
                            "contentType": "application/json",
                            "payloadUserPwd": {
                                "username": "alice",
                                "password": "secret",
                                "usernameField": "username",
                                "passwordField": "password",
                            },
                            "token": {
                                "extractFrom": "body",
                                "extractSelector": "/access_token",
                                "sendIn": "header",
                                "sendName": "Authorization",
                                "sendTemplate": "Bearer {token}",
                            },
                        },
                    }
                ]
            }
        ),
    )
    testdir.makefile(".toml", schemathesis='[auth.wfc]\npath = "auth.json"\n')
    testdir.makepyfile(
        """
import schemathesis
from flask import Flask, jsonify, request
from hypothesis import Phase, settings

app = Flask("test")

@app.route("/openapi.json")
def spec():
    return {
        "openapi": "3.0.0",
        "info": {"title": "Test", "version": "1.0"},
        "paths": {"/protected": {"get": {"responses": {"200": {"description": "OK"}}}}},
    }

@app.route("/api/login", methods=["POST"])
def login():
    assert request.get_json(force=True) == {"username": "alice", "password": "secret"}
    return jsonify({"access_token": "secret-token"})

@app.route("/protected")
def protected():
    if request.headers.get("Authorization") == "Bearer secret-token":
        return jsonify({"result": "ok"})
    return jsonify({"error": "unauthorized"}), 401

schema = schemathesis.openapi.from_wsgi("/openapi.json", app)

@schema.parametrize()
@settings(max_examples=1, phases=[Phase.generate])
def test_api(case):
    assert case.call().status_code == 200
"""
    )
    testdir.runpytest("-s").assert_outcomes(passed=1)


def test_wfc_cookie_over_wsgi(testdir):
    testdir.makefile(
        ".json",
        auth=json.dumps(
            {
                "auth": [
                    {
                        "name": "u",
                        "loginEndpointAuth": {"verb": "POST", "endpoint": "/api/login", "expectCookies": True},
                    }
                ]
            }
        ),
    )
    testdir.makefile(".toml", schemathesis='[auth.wfc]\npath = "auth.json"\n')
    testdir.makepyfile(
        """
import schemathesis
from flask import Flask, jsonify, request
from hypothesis import Phase, settings

app = Flask("test")

@app.route("/openapi.json")
def spec():
    return {
        "openapi": "3.0.0",
        "info": {"title": "Test", "version": "1.0"},
        "paths": {"/protected": {"get": {"responses": {"200": {"description": "OK"}}}}},
    }

@app.route("/api/login", methods=["POST"])
def login():
    response = jsonify({"ok": True})
    response.set_cookie("session", "sess-abc")
    return response

@app.route("/protected")
def protected():
    if request.cookies.get("session") == "sess-abc":
        return jsonify({"result": "ok"})
    return jsonify({"error": "unauthorized"}), 401

schema = schemathesis.openapi.from_wsgi("/openapi.json", app)

@schema.parametrize()
@settings(max_examples=1, phases=[Phase.generate])
def test_api(case):
    assert case.call().status_code == 200
"""
    )
    testdir.runpytest("-s").assert_outcomes(passed=1)


def test_wfc_login_over_asgi(testdir):
    testdir.makefile(
        ".json",
        auth=json.dumps(
            {
                "auth": [
                    {
                        "name": "u",
                        "loginEndpointAuth": {
                            "verb": "POST",
                            "endpoint": "/api/login",
                            "contentType": "application/json",
                            "payloadUserPwd": {
                                "username": "alice",
                                "password": "secret",
                                "usernameField": "username",
                                "passwordField": "password",
                            },
                            "token": {
                                "extractFrom": "body",
                                "extractSelector": "/access_token",
                                "sendIn": "header",
                                "sendName": "Authorization",
                                "sendTemplate": "Bearer {token}",
                            },
                        },
                    }
                ]
            }
        ),
    )
    testdir.makefile(".toml", schemathesis='[auth.wfc]\npath = "auth.json"\n')
    testdir.makepyfile(
        """
import schemathesis
from fastapi import FastAPI, Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from hypothesis import Phase, settings

app = FastAPI()
security = HTTPBearer(auto_error=False)

@app.post("/api/login", include_in_schema=False)
async def login(payload: dict):
    assert payload == {"username": "alice", "password": "secret"}
    return {"access_token": "secret-token"}

@app.get("/protected")
async def protected(credentials: HTTPAuthorizationCredentials = Depends(security)):
    if credentials is None or credentials.credentials != "secret-token":
        raise HTTPException(status_code=401, detail="Unauthorized")
    return {"result": "ok"}

schema = schemathesis.openapi.from_asgi("/openapi.json", app)

@schema.parametrize()
@settings(max_examples=1, phases=[Phase.generate])
def test_api(case):
    assert case.call().status_code == 200
"""
    )
    testdir.runpytest("-s").assert_outcomes(passed=1)
