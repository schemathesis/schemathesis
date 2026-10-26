from __future__ import annotations

import json
import os
from xml.etree import ElementTree

import pytest
from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from flask import Flask, jsonify, redirect, request

import schemathesis
from schemathesis.auths import AuthContext
from schemathesis.core.cache import Manifest, write
from schemathesis.core.cache.models import FORMAT_VERSION
from schemathesis.core.errors import LoaderError
from schemathesis.specs.openapi.object_authorization import apply_as
from schemathesis.wfc.converter import wfc_to_auth_provider
from schemathesis.wfc.errors import WFCLoginError, WFCTokenExtractionError, WFCValidationError
from schemathesis.wfc.escalation import escalating_provider, identity_assignments, restore_identity_assignments
from schemathesis.wfc.loader import load_from_dict
from test.apps.catalog.openapi import wfc as wfc_apps
from test.apps.catalog.openapi.wfc import WFC_PASSWORD, WFC_SESSION, WFC_TOKEN, WFC_USERNAME
from test.utils import allure_results

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


def test_wfc_user_requires_path(cli, tmp_path):
    schema = _write(
        tmp_path,
        {"openapi": "3.0.0", "info": {"title": "Test", "version": "1.0"}, "paths": {}},
        name="schema.json",
    )

    result = cli.run(schema, "--url=http://127.0.0.1", "--auth-wfc-user=bob")

    assert result.exit_code == 2
    assert result.stderr == (
        "Usage: schemathesis run [OPTIONS] LOCATION\n"
        "Try 'schemathesis run --help' for help.\n\n"
        "Error: --auth-wfc-user requires --auth-wfc or path under [auth.wfc]\n"
    )


def test_replay_wfc_user_requires_path(cli):
    result = cli.main("replay", "--auth-wfc-user=bob")

    assert result.exit_code == 2
    assert result.stderr == (
        "Usage: schemathesis replay [OPTIONS] [PATH]\n"
        "Try 'schemathesis replay --help' for help.\n\n"
        "Error: --auth-wfc-user requires --auth-wfc or path under [auth.wfc]\n"
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


def test_wfc_login_without_token_falls_back_to_cookies(cli, ctx, tmp_path):
    # WFC requires neither `token` nor `expectCookies`; a header-based login returning a session is valid.
    api = ctx.openapi.apps.wfc_login()
    auth = _write(tmp_path, {"auth": [{"name": "u", "loginEndpointAuth": _login()}]})

    assert _run_wfc(cli, api, auth).exit_code == 0
    assert all(f"session={WFC_SESSION}" in (_header(c, "Cookie") or "") for c in _protected_calls(api))


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


@pytest.mark.parametrize(
    ("cli_args", "wfc_config"),
    [
        (["--auth-wfc={path}", "--auth-wfc-user=bob"], None),
        (["--auth-wfc-user=bob"], {}),
        ([], {"user": "bob"}),
    ],
    ids=["cli-path-and-user", "config-path-cli-user", "config-path-and-user"],
)
def test_wfc_selects_named_user(cli, ctx, tmp_path, cli_args, wfc_config):
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
    config = None if wfc_config is None else {"auth": {"wfc": {"path": auth, **wfc_config}}}

    result = cli.run(api.schema_url, "--max-examples=5", *(arg.format(path=auth) for arg in cli_args), config=config)
    assert result.exit_code == 0
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


def test_wfc_wsgi_login_missing_header_lists_header_names():
    app = wfc_apps.wfc_login().server
    operation = schemathesis.openapi.from_wsgi("/openapi.json", app)["/api/protected"]["GET"]
    provider = _provider_for(_login(token=_token(extractFrom="header", extractSelector="X-Absent")))
    context = AuthContext(operation=operation, app=app)
    with pytest.raises(WFCTokenExtractionError) as exc:
        provider.get(operation.Case(), context)
    assert str(exc.value) == (
        "Header 'X-Absent' not found in response. "
        "Available headers: 'Content-Type', 'Content-Length', 'X-Auth-Token', 'Set-Cookie'"
    )


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


def _redirect_login_app(ctx) -> Flask:
    app, _ = ctx.openapi.make_flask_app({"/api/protected": {"get": {"responses": {"200": {"description": "OK"}}}}})

    @app.route("/api/login", methods=["POST"])
    def login() -> object:
        response = redirect("/api/protected", code=302)
        response.set_cookie("JSESSIONID", WFC_SESSION)
        return response

    @app.route("/api/protected", methods=["GET"])
    def protected() -> object:
        return jsonify({"ok": True})

    return app


def test_wfc_http_login_redirect_yields_cookies(ctx, app_runner):
    # Form-login servers answer a successful sign-in with a redirect carrying the session cookie.
    operation = schemathesis.openapi.from_url(app_runner.openapi_url(_redirect_login_app(ctx)))["/api/protected"]["GET"]
    provider = _provider_for(_login(expectCookies=True))
    context = AuthContext(operation=operation, app=None)
    case = operation.Case()
    provider.set(case, provider.get(case, context), context)
    assert case.cookies == {"JSESSIONID": WFC_SESSION}


def test_wfc_wsgi_login_redirect_yields_cookies(ctx):
    app = _redirect_login_app(ctx)
    operation = schemathesis.openapi.from_wsgi("/openapi.json", app)["/api/protected"]["GET"]
    provider = _provider_for(_login(expectCookies=True))
    context = AuthContext(operation=operation, app=app)
    case = operation.Case()
    provider.set(case, provider.get(case, context), context)
    assert case.cookies == {"JSESSIONID": WFC_SESSION}


def test_wfc_asgi_login_redirect_yields_cookies():
    app = FastAPI()

    @app.post("/api/login")
    def login() -> RedirectResponse:
        response = RedirectResponse("/api/protected", status_code=302)
        response.set_cookie("JSESSIONID", WFC_SESSION)
        return response

    @app.get("/api/protected")
    def protected() -> dict:
        return {"ok": True}

    operation = schemathesis.openapi.from_asgi("/openapi.json", app)["/api/protected"]["GET"]
    provider = _provider_for(_login(expectCookies=True))
    context = AuthContext(operation=operation, app=app)
    case = operation.Case()
    provider.set(case, provider.get(case, context), context)
    assert case.cookies == {"JSESSIONID": WFC_SESSION}


@pytest.mark.parametrize(
    ("set_cookie", "expected"),
    [
        ("user[id]=1; Path=/", {"user[id]": "1"}),
        ("a@b=1; Path=/", {"a@b": "1"}),
        ("sid=1; Path=/; Priority=High", {"sid": "1"}),
    ],
    ids=["brackets-in-name", "at-sign-in-name", "unknown-attribute"],
)
def test_wfc_wsgi_cookie_login_reads_set_cookie_like_http(ctx, set_cookie, expected):
    app, _ = ctx.openapi.make_flask_app({"/api/protected": {"get": {"responses": {"200": {"description": "OK"}}}}})

    @app.route("/api/login", methods=["POST"])
    def login() -> object:
        response = jsonify({})
        response.headers.add("Set-Cookie", set_cookie)
        return response

    operation = schemathesis.openapi.from_wsgi("/openapi.json", app)["/api/protected"]["GET"]
    provider = _provider_for(_login(expectCookies=True))
    context = AuthContext(operation=operation, app=app)
    case = operation.Case()
    provider.set(case, provider.get(case, context), context)
    assert case.cookies == expected


def test_wfc_login_endpoint_is_resolved_against_the_server_root(ctx, app_runner):
    app, _ = ctx.openapi.make_flask_app(
        {"/protected": {"get": {"responses": {"200": {"description": "OK"}}}}},
        servers=[{"url": "/app"}],
    )

    @app.route("/app/login", methods=["POST"])
    def login() -> object:
        return jsonify({"access_token": WFC_TOKEN})

    @app.route("/app/protected", methods=["GET"])
    def protected() -> object:
        return jsonify({"ok": True})

    operation = schemathesis.openapi.from_url(app_runner.openapi_url(app))["/protected"]["GET"]
    provider = _provider_for(_login(token=_token(), endpoint="/app/login"))
    context = AuthContext(operation=operation, app=None)
    case = operation.Case()
    provider.set(case, provider.get(case, context), context)
    assert case.headers["Authorization"] == f"Bearer {WFC_TOKEN}"


def _bouncing_login_app(ctx) -> Flask:
    # Form-login servers hand out a session cookie on rejected credentials too, so only the target tells them apart.
    app, _ = ctx.openapi.make_flask_app({"/api/protected": {"get": {"responses": {"200": {"description": "OK"}}}}})

    @app.route("/api/login", methods=["POST"])
    def login() -> object:
        response = redirect("/api/login?error", code=302)
        response.set_cookie("JSESSIONID", WFC_SESSION)
        return response

    return app


def test_wfc_http_login_redirect_back_to_login_is_rejected(ctx, app_runner):
    operation = schemathesis.openapi.from_url(app_runner.openapi_url(_bouncing_login_app(ctx)))["/api/protected"]["GET"]
    provider = _provider_for(_login(expectCookies=True))
    context = AuthContext(operation=operation, app=None)
    with pytest.raises(WFCLoginError, match="redirected back to the login endpoint"):
        provider.get(operation.Case(), context)


def test_wfc_wsgi_login_redirect_back_to_login_is_rejected(ctx):
    app = _bouncing_login_app(ctx)
    operation = schemathesis.openapi.from_wsgi("/openapi.json", app)["/api/protected"]["GET"]
    provider = _provider_for(_login(expectCookies=True))
    context = AuthContext(operation=operation, app=app)
    with pytest.raises(WFCLoginError, match="redirected back to the login endpoint"):
        provider.get(operation.Case(), context)


def test_wfc_external_login_redirect_back_to_login_is_rejected(ctx, app_runner):
    base_url = app_runner.openapi_url(_bouncing_login_app(ctx), path="")
    operation = schemathesis.openapi.from_url(f"{base_url}/openapi.json")["/api/protected"]["GET"]
    provider = _provider_for(
        {
            "verb": "POST",
            "externalEndpointURL": f"{base_url}/api/login",
            "contentType": "application/json",
            "payloadUserPwd": CREDENTIALS,
            "expectCookies": True,
        }
    )
    context = AuthContext(operation=operation, app=None)
    with pytest.raises(WFCLoginError, match="redirected back to the login endpoint"):
        provider.get(operation.Case(), context)


@pytest.mark.parametrize("location", [None, "http://a]b/x"], ids=["no-location", "malformed-location"])
def test_wfc_login_redirect_without_location_yields_cookies(ctx, location):
    app, _ = ctx.openapi.make_flask_app({"/api/protected": {"get": {"responses": {"200": {"description": "OK"}}}}})
    inner = app.wsgi_app

    # Werkzeug refuses to emit a Location that URL parsing rejects, so the login answer is written raw.
    def wsgi_app(environ, start_response):
        if environ["PATH_INFO"] != "/api/login":
            return inner(environ, start_response)
        headers = [("Set-Cookie", f"JSESSIONID={WFC_SESSION}; Path=/")]
        if location is not None:
            headers.append(("Location", location))
        start_response("302 Found", headers)
        return [b""]

    app.wsgi_app = wsgi_app

    operation = schemathesis.openapi.from_wsgi("/openapi.json", app)["/api/protected"]["GET"]
    provider = _provider_for(_login(expectCookies=True))
    context = AuthContext(operation=operation, app=app)
    case = operation.Case()
    provider.set(case, provider.get(case, context), context)
    assert case.cookies == {"JSESSIONID": WFC_SESSION}


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


@pytest.mark.parametrize(
    "login",
    [
        {
            "verb": "POST",
            "externalEndpointURL": "http://127.0.0.1:1/api/login",
            "contentType": "application/json",
            "payloadUserPwd": CREDENTIALS,
            "token": _token(),
        },
        {
            "verb": "POST",
            "externalEndpointURL": "http://[::1/api/login",
            "contentType": "application/json",
            "payloadUserPwd": CREDENTIALS,
            "token": _token(),
        },
        _login(token=_token(), headers=[{"name": "X-Tenant", "value": "Zürich€"}]),
    ],
    ids=["connection-refused", "malformed-url", "non-latin1-header"],
)
def test_wfc_login_connection_error(cli, ctx, tmp_path, login):
    api = ctx.openapi.apps.wfc_login()
    auth = _write(tmp_path, {"auth": [{"name": "u", "loginEndpointAuth": login}]})

    result = _run_wfc(cli, api, auth, max_examples=1)
    assert result.exit_code != 0
    assert "login endpoint call failed" in result.stdout.lower()
    assert "Traceback" not in result.stdout


def test_wfc_multiple_users_without_selection(cli, ctx, tmp_path):
    # Most Web Fuzzing Dataset files list several users without naming a default.
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

    assert _run_wfc(cli, api, auth).exit_code == 0
    assert all(_header(c, "X") == "1" for c in _protected_calls(api))


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


# Shape used by most authenticated APIs in the Web Fuzzing Dataset.
WFD_TEMPLATE_DOC = {
    "auth": [
        {"name": "admin", "loginEndpointAuth": {"payloadRaw": '{"user": "admin", "password": "bar"}'}},
        {"name": "user", "loginEndpointAuth": {"payloadRaw": '{"user": "user", "password": "bar"}'}},
    ],
    "authTemplate": {
        "loginEndpointAuth": {
            "endpoint": "/api/auth/signin",
            "verb": "POST",
            "contentType": "application/json",
            "token": {
                "extractFrom": "body",
                "extractSelector": "/accessToken",
                "sendName": "Authorization",
                "sendIn": "header",
                "sendTemplate": "Bearer {token}",
            },
        }
    },
}


def test_auth_template_fills_fields_entries_omit():
    entries = load_from_dict(WFD_TEMPLATE_DOC)

    assert [entry.name for entry in entries] == ["admin", "user"]
    for entry in entries:
        login = entry.login_endpoint_auth
        assert login is not None
        assert login.endpoint == "/api/auth/signin"
        assert login.verb == "POST"
        assert login.content_type == "application/json"
        assert login.token is not None
        assert login.token.send_template == "Bearer {token}"
    assert entries[0].login_endpoint_auth.payload_raw == '{"user": "admin", "password": "bar"}'


def test_auth_entry_wins_over_the_template():
    doc = {
        "auth": [{"name": "admin", "loginEndpointAuth": {"endpoint": "/own", "payloadRaw": "{}"}}],
        "authTemplate": {"loginEndpointAuth": {"endpoint": "/shared", "verb": "POST", "expectCookies": True}},
    }

    [entry] = load_from_dict(doc)

    assert entry.login_endpoint_auth.endpoint == "/own"
    assert entry.login_endpoint_auth.verb == "POST"


def test_auth_template_rejects_non_object_entry():
    doc = {
        "auth": ["admin"],
        "authTemplate": {"loginEndpointAuth": {"endpoint": "/shared", "verb": "POST", "expectCookies": True}},
    }

    with pytest.raises(WFCValidationError, match='"admin" is not of type "object"'):
        load_from_dict(doc)


ROLE_AUTH = {
    "auth": [
        {"name": "viewer", "fixedHeaders": [{"name": "Authorization", "value": "ApiKey viewer"}]},
        {"name": "editor", "fixedHeaders": [{"name": "Authorization", "value": "ApiKey editor"}]},
        {"name": "admin", "fixedHeaders": [{"name": "Authorization", "value": "ApiKey admin"}]},
    ]
}


def _identities(api, method, prefix):
    return {
        (r.headers.get("Authorization") or "").removeprefix("ApiKey ")
        for r in api.requests
        if r.method == method and r.path.startswith(prefix)
    }


def test_escalation_settles_each_operation_on_its_identity(cli, ctx, tmp_path):
    api = ctx.openapi.apps.wfc_role_gated()
    auth = _write(tmp_path, ROLE_AUTH)

    cli.run(api.schema_url, "--max-examples=20", f"--auth-wfc={auth}", "--phases=fuzzing")

    assert _identities(api, "GET", "/api/open") == {"viewer"}
    assert "admin" not in _identities(api, "DELETE", "/api/editor-only")
    assert _identities(api, "DELETE", "/api/admin-only") == {"viewer", "", "editor", "admin"}
    # A 404 says the id was wrong, not that the identity was accepted.
    assert "admin" in _identities(api, "DELETE", "/api/missing-first")
    # Payload rejections arrive interleaved with role denials and must not block escalation.
    assert _identities(api, "POST", "/api/validated") == {"viewer", "", "editor", "admin"}
    seen = [
        (r.headers.get("Authorization") or "").removeprefix("ApiKey ")
        for r in api.requests
        if r.method == "DELETE" and r.path.startswith("/api/nobody")
    ]
    assert seen, "operation was never dispatched"
    # Every identity is refused; the last one to reach authorization is the one a later request has any chance with.
    assert seen[-1] == "admin", f"settled on a weaker identity: {seen}"


EXPIRING_AUTH = {
    "auth": [
        {"name": "one", "loginEndpointAuth": {"payloadRaw": '{"user": "one"}'}},
        {"name": "two", "loginEndpointAuth": {"payloadRaw": '{"user": "two"}'}},
    ],
    "authTemplate": {
        "loginEndpointAuth": {
            "verb": "POST",
            "endpoint": "/api/login",
            "contentType": "application/json",
            "token": _token(),
        }
    },
}


def test_escalating_provider_exposes_its_caching_providers(ctx, tmp_path):
    # Reactive reauth is driven off the caching providers reachable from the schema; an escalating
    # provider must not hide the ones it wraps, or a stale token can never be refreshed.
    auth = _write(tmp_path, EXPIRING_AUTH)
    schema = ctx.openapi.load_schema(
        {"/api/protected": {"get": {"responses": {"200": {"description": "OK"}}}}},
        config=schemathesis.Config.from_dict({"auth": {"wfc": {"path": auth}}}),
    )

    assert 401 in schema.reauth_retry_statuses


def test_expired_token_is_refreshed_for_a_multi_user_document(cli, ctx, tmp_path):
    # The identity stays valid; only its token dies. Without reactive reauth the run replays the dead
    # token for the rest of the budget instead of logging in again.
    api = ctx.openapi.apps.wfc_expiring_token()
    auth = _write(tmp_path, EXPIRING_AUTH)

    cli.run(api.schema_url, f"--auth-wfc={auth}", "--phases=examples")

    protected = [r for r in api.requests if r.path == "/api/protected"]
    logins = [r.json().get("user") for r in api.requests if r.path == "/api/login"]
    assert len(protected) > 1, f"need repeat requests to outlive the token, got {len(protected)}"
    assert len(logins) > 1, f"token never refreshed: {logins}"
    assert set(logins) == {"one"}, f"escalated away from a refreshable identity: {logins}"


def test_escalation_walks_the_chain_on_401(cli, ctx, tmp_path):
    # A 401 that survives the reauth replay means the identity is wrong, not that its token expired.
    api = ctx.openapi.apps.wfc_role_gated_401()
    auth = _write(tmp_path, ROLE_AUTH)

    cli.run(api.schema_url, "--max-examples=8", f"--auth-wfc={auth}", "--phases=fuzzing")

    assert _identities(api, "DELETE", "/api/admin-only") == {"viewer", "", "editor", "admin"}


def test_denials_count_against_the_identity_that_was_sent(cli, ctx, tmp_path):
    # Coverage cases carry their identity from generation, so many refusals of one user arrive at once.
    api = ctx.openapi.apps.wfc_role_gated()
    auth = _write(
        tmp_path,
        {"auth": [ROLE_AUTH["auth"][0], ROLE_AUTH["auth"][2], ROLE_AUTH["auth"][1]]},
    )

    cli.run(api.schema_url, "--max-examples=8", f"--auth-wfc={auth}", "--phases=coverage,fuzzing")

    assert "admin" in _identities(api, "DELETE", "/api/admin-only")


def test_content_type_probe_refusal_does_not_move_the_identity(cli, ctx, tmp_path):
    # A server may refuse a malformed request before authorizing it; that says nothing about the identity.
    app, _ = ctx.openapi.make_flask_app(
        {"/items": {"get": {"responses": {"200": {"description": "OK"}, "403": {"description": "Forbidden"}}}}}
    )
    sent = []

    @app.route("/items")
    def items():
        sent.append(request.headers.get("Authorization", ""))
        content_type = request.headers.get("Content-Type", "")
        if content_type.startswith("multipart/form-data") and "boundary=" not in content_type:
            return jsonify({}), 403
        return jsonify({}), 200

    auth = _write(tmp_path, ROLE_AUTH)
    cli.run_openapi_app(app, "--max-examples=5", f"--auth-wfc={auth}", "--phases=coverage,fuzzing")

    assert set(sent) == {"ApiKey viewer"}


BASIC_AUTH = {
    "auth": [
        {"name": "alice", "fixedHeaders": [{"name": "Authorization", "value": "Basic YWxpY2U6c2VjcmV0"}]},
        {"name": "bob", "fixedHeaders": [{"name": "Authorization", "value": "Basic Ym9iOnNlY3JldA=="}]},
    ]
}


@pytest.mark.parametrize(
    ("version", "scheme"),
    [
        ("2.0", {"type": "basic"}),
        ("3.0.2", {"type": "http", "scheme": "basic"}),
        ("3.0.2", {"type": "http", "scheme": "bearer"}),
    ],
    ids=["swagger-basic", "openapi-basic", "openapi-bearer"],
)
def test_case_without_credentials_does_not_move_the_identity(ctx, app_runner, tmp_path, version, scheme):
    # The coverage phase drops the credentials on purpose, so its 401 says nothing about the identity.
    responses = {"200": {"description": "OK"}, "401": {"description": "Unauthorized"}}
    security = (
        {"securityDefinitions": {"auth": scheme}, "basePath": "/"}
        if version == "2.0"
        else {"components": {"securitySchemes": {"auth": scheme}}}
    )
    app, _ = ctx.openapi.make_flask_app(
        {"/api/first": {"get": {"responses": responses}}, "/api/orders": {"get": {"responses": responses}}},
        version=version,
        security=[{"auth": []}],
        **security,
    )

    @app.route("/api/first", endpoint="first")
    @app.route("/api/orders", endpoint="orders")
    def protected():
        return jsonify({}), 200 if request.headers.get("Authorization") else 401

    config = schemathesis.Config.from_dict({"auth": {"wfc": {"path": _write(tmp_path, BASIC_AUTH)}}})
    schema = schemathesis.openapi.from_url(app_runner.openapi_url(app), config=config)
    schema.config.phases.fuzzing.enabled = False
    schema.config.phases.stateful.enabled = False
    list(schemathesis.engine.from_schema(schema).execute())

    provider = escalating_provider(schema)
    assert [provider.names[provider.index_for(label)] for label in ("GET /api/first", "GET /api/orders")] == [
        "alice",
        "alice",
    ]


def test_identity_refused_before_accounts_exist_is_retried(cli, ctx, tmp_path):
    # An API may create its accounts mid-run, so a 401 from before then says nothing about the identity.
    api = ctx.openapi.apps.wfc_accounts_seeded_later()
    auth = _write(tmp_path, {"auth": [ROLE_AUTH["auth"][2], ROLE_AUTH["auth"][0], ROLE_AUTH["auth"][1]]})

    cli.run(
        api.schema_url, "--max-examples=8", f"--auth-wfc={auth}", "--phases=coverage,fuzzing", "--continue-on-failure"
    )

    seen = [
        (r.headers.get("Authorization") or "").removeprefix("ApiKey ")
        for r in api.requests
        if r.method == "DELETE" and r.path.startswith("/api/admin-only")
    ]
    assert seen, "operation was never dispatched"
    assert seen[-1] == "admin", f"never retried the identity refused before seeding: {seen}"


def test_pinned_user_never_escalates(cli, ctx, tmp_path):
    api = ctx.openapi.apps.wfc_role_gated()
    auth = _write(tmp_path, ROLE_AUTH)

    cli.run(api.schema_url, "--max-examples=8", f"--auth-wfc={auth}", "--phases=fuzzing", "--auth-wfc-user=viewer")
    assert _identities(api, "DELETE", "/api/admin-only") == {"viewer"}


def test_assignment_is_restored_from_cache(cli, ctx, tmp_path):
    # A second run starts where the first settled instead of walking the chain again.
    api = ctx.openapi.apps.wfc_role_gated()
    auth = _write(tmp_path, ROLE_AUTH)
    cache_dir = tmp_path / "cache"
    config = {"cache": {"directory": str(cache_dir)}, "auth": {"wfc": {"path": auth}}}

    cli.run(api.schema_url, "--max-examples=8", "--phases=fuzzing", config=config)
    first = len(api.requests)
    cli.run(api.schema_url, "--max-examples=8", "--phases=fuzzing", config=config)

    second = [r for r in api.requests[first:] if r.method == "DELETE" and "admin-only" in r.path]
    assert second, "second run never reached the gated operation"
    assert {(r.headers.get("Authorization") or "").removeprefix("ApiKey ") for r in second} == {"admin"}


@pytest.mark.snapshot(replace_reproduce_with=True)
def test_failure_names_the_identity(cli, ctx, tmp_path, snapshot_cli):
    # With several identities in play, a report that does not say which one ran is ambiguous.
    # The name alone is not actionable; the flag that reproduces it is.
    api = ctx.openapi.apps.wfc_role_gated()
    auth = _write(tmp_path, ROLE_AUTH)

    assert (
        cli.run(
            api.schema_url,
            "--max-examples=8",
            "--phases=fuzzing",
            f"--auth-wfc={auth}",
            "-c",
            "not_a_server_error",
            "--include-path=/api/admin-boom/{itemId}",
        )
        == snapshot_cli
    )


def test_ndjson_records_the_identity(cli, ctx, tmp_path):
    # Sanitization filters the header, so without the name the report cannot tell identities apart.
    api = ctx.openapi.apps.wfc_role_gated()
    auth = _write(tmp_path, ROLE_AUTH)
    report = tmp_path / "events.ndjson"

    cli.run(
        api.schema_url,
        "--max-examples=8",
        "--phases=fuzzing",
        f"--auth-wfc={auth}",
        "-c",
        "not_a_server_error",
        "--report",
        "ndjson",
        "--report-ndjson-path",
        str(report),
    )

    identities = {
        node.get("value", {}).get("auth_identity")
        for line in report.read_text().splitlines()
        if line
        for node in json.loads(line).get("ScenarioFinished", {}).get("recorder", {}).get("cases", {}).values()
    }
    assert "admin" in identities
    seen = [
        (r.headers.get("Authorization") or "").removeprefix("ApiKey ")
        for r in api.requests
        if r.method == "DELETE" and r.path.startswith("/api/nobody")
    ]
    assert seen, "operation was never dispatched"
    # No identity can reach this operation and dropping credentials is no better, so it stops moving.
    assert len(set(seen[-3:])) == 1, f"kept cycling through the chain: {seen}"


def test_junit_and_allure_name_the_identity(cli, ctx, tmp_path):
    api = ctx.openapi.apps.wfc_role_gated()
    auth = _write(tmp_path, ROLE_AUTH)
    junit = tmp_path / "junit.xml"
    allure = tmp_path / "allure-results"

    cli.run(
        api.schema_url,
        "--max-examples=8",
        "--phases=fuzzing",
        f"--auth-wfc={auth}",
        "-c",
        "not_a_server_error",
        "--include-path=/api/admin-boom/{itemId}",
        f"--report-junit-path={junit}",
        f"--report-allure-path={allure}",
    )

    line = "Identity: admin (reproduce with --auth-wfc-user admin)"
    assert [failure.text for failure in ElementTree.parse(junit).iter("failure") if line in failure.text] != []
    messages = [
        step["statusDetails"]["message"] for result in allure_results(allure) for step in result.get("steps", [])
    ]
    assert [message for message in messages if line in message] != []


def test_unknown_cached_identity_is_ignored(cli, ctx, tmp_path):
    # The auth file may have been edited since the cache was written.
    api = ctx.openapi.apps.wfc_role_gated()
    auth = _write(tmp_path, ROLE_AUTH)
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    manifest = Manifest(
        format_version=FORMAT_VERSION,
        schemathesis_version="0.0.0",
        schema_location=api.schema_url,
        base_url=api.base_url,
        created_at="2026-01-01T00:00:00Z",
        auth_identities={"GET /api/open": "ghost"},
    )
    write(cache_dir, manifest, [])

    cli.run(
        api.schema_url,
        "--max-examples=8",
        "--phases=fuzzing",
        "-c",
        "not_a_server_error",
        config={"cache": {"directory": str(cache_dir)}, "auth": {"wfc": {"path": auth}}},
    )

    assert _identities(api, "GET", "/api/open") == {"viewer"}


def test_exhausted_chain_keeps_the_identity_that_got_furthest(cli, ctx, tmp_path):
    # A 403 reached authorization; dropping credentials only earns a 401, so it is not an improvement.
    api = ctx.openapi.apps.wfc_role_gated_401_when_anonymous()
    auth = _write(tmp_path, ROLE_AUTH)

    cli.run(api.schema_url, "--max-examples=12", "--phases=fuzzing", f"--auth-wfc={auth}", "-c", "not_a_server_error")

    seen = [
        (r.headers.get("Authorization") or "").removeprefix("ApiKey ")
        for r in api.requests
        if r.method == "DELETE" and r.path.startswith("/api/nobody")
    ]
    assert seen, "operation was never dispatched"
    assert seen[-1] != ""


def test_credentials_that_never_work_reach_anonymous_early(cli, ctx, tmp_path):
    # Walking every identity first spends a short run discovering what the second request could show.
    api = ctx.openapi.apps.wfc_credentials_rejected()
    auth = _write(tmp_path, ROLE_AUTH)

    cli.run(api.schema_url, "--max-examples=2", f"--auth-wfc={auth}", "--phases=fuzzing")

    public = [r for r in api.requests if r.path.startswith("/api/public")]
    assert public, "operation was never called"
    assert any(r.headers.get("Authorization") is None for r in public)


PEER_AUTH = {
    "auth": [
        {
            "name": "alice",
            "fixedHeaders": [
                {"name": "Authorization", "value": "ApiKey alice"},
                {"name": "X-Tenant", "value": "alice"},
            ],
        },
        {"name": "bob", "fixedHeaders": [{"name": "Authorization", "value": "ApiKey bob"}]},
        {"name": "admin", "fixedHeaders": [{"name": "Authorization", "value": "ApiKey admin"}]},
    ]
}


def _peer_schema(ctx, tmp_path, *, peers=("alice", "bob"), **wfc):
    config = {"path": _write(tmp_path, PEER_AUTH), "peers": list(peers), **wfc}
    return ctx.openapi.load_schema(
        {"/api/protected": {"get": {"responses": {"200": {"description": "OK"}}}}},
        config=schemathesis.Config.from_dict({"auth": {"wfc": config}}),
    )


def _authenticated_case(schema):
    operation = schema["/api/protected"]["GET"]
    case = operation.Case()
    schema.auth.set(case, AuthContext(operation=operation, app=None))
    return case


def test_apply_as_sends_only_the_peer_credentials(ctx, tmp_path):
    schema = _peer_schema(ctx, tmp_path)
    case = _authenticated_case(schema)

    probe = apply_as(case, escalating_provider(schema).provider_for("bob"), "bob")

    assert (dict(probe.headers), probe._auth_identity) == ({"Authorization": "ApiKey bob"}, "bob")
    assert dict(case.headers) == {"Authorization": "ApiKey alice", "X-Tenant": "alice"}


def test_pinned_user_with_peers_tags_cases(ctx, tmp_path):
    schema = _peer_schema(ctx, tmp_path, user="bob")
    escalating_provider(schema).record("GET /api/protected", "bob", 403)

    assert _authenticated_case(schema)._auth_identity == "bob"


def test_pinned_user_ignores_persisted_assignments(ctx, tmp_path):
    schema = _peer_schema(ctx, tmp_path, user="bob")
    restore_identity_assignments(schema, {"GET /api/protected": "alice"})

    assert (identity_assignments(schema), _authenticated_case(schema)._auth_identity) == ({}, "bob")


@pytest.mark.parametrize(
    "login",
    [_login(token=_token(sendIn="query", sendName="token", sendTemplate="{token}")), _login(expectCookies=True)],
    ids=["query-token", "cookie"],
)
def test_apply_as_strips_login_credentials(ctx, tmp_path, login):
    api = ctx.openapi.apps.wfc_login()
    doc = {
        "auth": [
            {"name": "alice", "loginEndpointAuth": login},
            {"name": "bob", "fixedHeaders": [{"name": "Authorization", "value": "ApiKey bob"}]},
        ]
    }
    config = {"path": _write(tmp_path, doc), "peers": ["alice", "bob"]}
    schema = schemathesis.openapi.from_url(
        api.schema_url, config=schemathesis.Config.from_dict({"auth": {"wfc": config}})
    )
    case = _authenticated_case(schema)

    probe = apply_as(case, escalating_provider(schema).provider_for("bob"), "bob")

    assert (dict(probe.headers), probe.query, probe.cookies) == ({"Authorization": "ApiKey bob"}, {}, {})


def test_probe_is_claimed_once(ctx, tmp_path):
    provider = escalating_provider(_peer_schema(ctx, tmp_path))
    key = ("GET /api/orders/{id}", "Order", "bob")

    assert [provider.claim_probe(key), provider.claim_probe(key)] == [True, False]


def test_unknown_peer_is_rejected(ctx, tmp_path):
    with pytest.raises(LoaderError, match="Peer 'carol' not found in WFC auth document"):
        _peer_schema(ctx, tmp_path, peers=("alice", "carol"))


@pytest.mark.snapshot(replace_reproduce_with=True)
@pytest.mark.parametrize("peers", [["alice"], ["alice", "alice"]], ids=["single", "duplicate"])
def test_peers_need_two_distinct_users(cli, ctx, tmp_path, snapshot_cli, peers):
    api = ctx.openapi.apps.wfc_login()
    assert _run_wfc(cli, api, _write(tmp_path, PEER_AUTH), peers=peers) == snapshot_cli
