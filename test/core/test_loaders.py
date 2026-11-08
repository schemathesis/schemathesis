import ssl
import threading

import pytest
from django.conf import settings
from django.core.asgi import get_asgi_application
from django.core.wsgi import get_wsgi_application
from django.http import HttpResponse
from django.test import override_settings
from django.urls import path
from flask import Flask, Response, jsonify, redirect, request

import schemathesis
from schemathesis.core.errors import LoaderError, LoaderErrorKind
from schemathesis.core.transport import USER_AGENT
from test.apps.catalog.graphql import bookstore
from test.apps.catalog.openapi import basic
from test.utils import graphql_url, openapi_url, refusing_url


@pytest.mark.parametrize(
    "loader",
    [
        schemathesis.openapi.from_asgi,
        schemathesis.openapi.from_wsgi,
        schemathesis.graphql.from_asgi,
        schemathesis.graphql.from_wsgi,
    ],
    ids=["openapi-asgi", "openapi-wsgi", "graphql-asgi", "graphql-wsgi"],
)
def test_absolute_urls_for_apps(loader):
    # When an absolute URL passed to a ASGI / WSGI loader
    # Then it should be rejected
    with pytest.raises(ValueError, match="Schema path should be relative for WSGI/ASGI loaders"):
        loader("http://127.0.0.1:1/schema.json", app=None)  # actual app doesn't matter here


@pytest.mark.parametrize(
    ("loader", "make_url"),
    [
        (schemathesis.openapi.from_url, openapi_url),
        (schemathesis.graphql.from_url, graphql_url),
    ],
)
@pytest.mark.parametrize("base_url", ["http://example.com/", "http://example.com"])
def test_base_url_override(ctx, loader, make_url, base_url):
    url = make_url(ctx)
    # When the user overrides base_url
    schema = loader(url)
    schema.config.update(base_url=base_url)
    operation = next(schema.get_all_operations()).ok()
    # Then the overridden value should not have a trailing slash
    assert operation.base_url == "http://example.com"


@pytest.mark.parametrize(
    ("app", "path", "loader"),
    [
        (basic.success, "/openapi.json", schemathesis.openapi.from_url),
        (bookstore.books, "/graphql", schemathesis.graphql.from_url),
    ],
    ids=["openapi", "graphql"],
)
# The server's TLS certificate is untrusted, so loading succeeds only if `verify=False` is forwarded.
def test_uri_loader_custom_kwargs(app_runner, app, path, loader):
    server = app().server
    received = []
    server.before_request(lambda: received.append(dict(request.headers)))
    port = app_runner.run_https_flask_app(server)
    loader(f"https://127.0.0.1:{port}{path}", verify=False, headers={"X-Test": "foo"})
    assert [(headers["X-Test"], headers["User-Agent"]) for headers in received] == [("foo", USER_AGENT)]


@pytest.mark.parametrize(
    ("app", "path", "loader"),
    [
        (basic.success, "/openapi.json", schemathesis.openapi.from_url),
        (bookstore.books, "/graphql", schemathesis.graphql.from_url),
    ],
    ids=["openapi", "graphql"],
)
def test_uri_loader_transport_from_config(app_runner, tmp_path, app, path, loader):
    server = app().server
    received = []
    server.before_request(
        lambda: received.append(
            (
                request.headers.get("X-Test"),
                request.headers.get("Authorization"),
                ssl.PEM_cert_to_DER_cert(request.environ["SSL_CLIENT_CERT"]),
            )
        )
    )
    running = app_runner.run_client_cert_flask_app(server, tmp_path)
    config = schemathesis.Config.from_dict(
        {
            "tls-verify": False,
            "request-cert": str(running.cert_path),
            "headers": {"X-Test": "foo"},
            "auth": {"basic": {"username": "test", "password": "test"}},
        }
    )
    loader(f"https://127.0.0.1:{running.port}{path}", config=config)
    assert received == [("foo", "Basic dGVzdDp0ZXN0", running.cert_der)]


@pytest.mark.parametrize(
    ("app", "path", "loader"),
    [
        (basic.success, "/openapi.json", schemathesis.openapi.from_url),
        (bookstore.books, "/graphql", schemathesis.graphql.from_url),
    ],
    ids=["openapi", "graphql"],
)
# The schema URL refuses connections, so loading succeeds only through the proxy, which answers as the API itself.
def test_uri_loader_proxy_from_config(app_runner, app, path, loader):
    proxy = f"http://127.0.0.1:{app_runner.run_flask_app(app().server)}"
    config = schemathesis.Config.from_dict({"proxy": proxy})
    with refusing_url() as url:
        loader(f"{url}{path}", config=config)


def test_uri_loader_explicit_kwargs_override_config(ctx):
    api = ctx.openapi.apps.success()
    config = schemathesis.Config.from_dict({"headers": {"X-Test": "config"}})
    schemathesis.openapi.from_url(api.schema_url, config=config, headers={"X-Test": "explicit"})
    assert api.schema_requests[0].headers["X-Test"] == "explicit"


def test_auth_loader_options(ctx):
    api = ctx.openapi.apps.success()
    schemathesis.openapi.from_url(api.schema_url, auth=("test", "test"))
    assert api.schema_requests[0].headers["Authorization"] == "Basic dGVzdDp0ZXN0"


@pytest.mark.parametrize(
    ("headers", "expected"),
    [({}, USER_AGENT), ({"user-agent": "custom/1.0"}, "custom/1.0")],
    ids=["default", "custom"],
)
def test_loader_user_agent(ctx, headers, expected):
    api = ctx.openapi.apps.success()
    schemathesis.openapi.from_url(api.schema_url, headers=headers)
    assert api.schema_requests[0].headers["User-Agent"] == expected


def test_redirect_loop_reported_as_loader_error(app_runner):
    app = Flask(__name__)

    @app.route("/openapi.json")
    def openapi_spec():
        return redirect("/openapi.json")

    url = app_runner.openapi_url(app)
    with pytest.raises(LoaderError, match="Exceeded 30 redirects"):
        schemathesis.openapi.from_url(url)


def test_wait_for_schema_retries_on_read_timeout(ctx, app_runner):
    # A slow-to-respond server (read timeout) must be retried within the wait budget,
    # the same way connection refused or HTTP 503 is.
    schema = ctx.openapi.build_schema({"/x": {"get": {"responses": {"200": {"description": "OK"}}}}})
    call_count = [0]
    retried = threading.Event()
    app = Flask(__name__)

    @app.route("/openapi.json")
    def openapi_spec():
        call_count[0] += 1
        if call_count[0] == 1:
            retried.wait(timeout=10)
        else:
            retried.set()
        return jsonify(schema)

    url = app_runner.openapi_url(app)
    loaded = schemathesis.openapi.from_url(url, wait_for_schema=10, timeout=1)
    assert loaded.raw_schema == schema
    assert call_count[0] == 2


# The headers arrive at once, then the body stalls past the timeout.
def test_schema_body_read_timeout_reported_as_timeout(app_runner):
    release = threading.Event()
    app = Flask(__name__)

    @app.route("/openapi.json")
    def openapi_spec():
        def stall_mid_body():
            yield "{"
            release.wait()
            yield "}"

        return Response(stall_mid_body(), content_type="application/json")

    url = app_runner.openapi_url(app)
    try:
        with pytest.raises(LoaderError) as exc_info:
            schemathesis.openapi.from_url(url, timeout=0.2)
    finally:
        release.set()
    assert (exc_info.value.kind, exc_info.value.message, exc_info.value.extras) == (
        LoaderErrorKind.NETWORK_OTHER,
        "Read timed out after 0.2 seconds",
        [],
    )


COMMON_MIDDLEWARE = "django.middleware.common.CommonMiddleware"


def bad_request(request):
    return HttpResponse(status=400)


urlpatterns = [path("schema", bad_request)]


@pytest.fixture
def django_settings():
    if not settings.configured:
        settings.configure(ROOT_URLCONF=__name__, ALLOWED_HOSTS=["*"], SECRET_KEY="schemathesis-test")


DISALLOWED_HOST_MESSAGE = """Failed to load schema due to client error (HTTP 400 Bad Request)

Django rejected the request because its `Host` header is not in `ALLOWED_HOSTS`

    Host:          {host}
    ALLOWED_HOSTS: {allowed_hosts}

Add '{host}' to ALLOWED_HOSTS in the Django settings you use for testing"""


@pytest.mark.parametrize(
    ("loader", "make_app", "host"),
    [
        (schemathesis.openapi.from_wsgi, get_wsgi_application, "localhost"),
        (schemathesis.openapi.from_asgi, get_asgi_application, "testserver"),
        (schemathesis.graphql.from_wsgi, get_wsgi_application, "localhost"),
        (schemathesis.graphql.from_asgi, get_asgi_application, "testserver"),
    ],
    ids=["openapi-wsgi", "openapi-asgi", "graphql-wsgi", "graphql-asgi"],
)
def test_django_disallowed_host(django_settings, loader, make_app, host):
    with override_settings(
        ROOT_URLCONF=__name__, ALLOWED_HOSTS=["api.example.com"], DEBUG=False, MIDDLEWARE=[COMMON_MIDDLEWARE]
    ):
        app = make_app()
        with pytest.raises(LoaderError) as exc:
            loader("/schema", app)
    assert str(exc.value) == DISALLOWED_HOST_MESSAGE.format(host=host, allowed_hosts="['api.example.com']")


def test_django_disallowed_host_with_debug(django_settings):
    with override_settings(ROOT_URLCONF=__name__, ALLOWED_HOSTS=[], DEBUG=True, MIDDLEWARE=[COMMON_MIDDLEWARE]):
        app = get_asgi_application()
        with pytest.raises(LoaderError) as exc:
            schemathesis.openapi.from_asgi("/schema", app)
    assert str(exc.value) == DISALLOWED_HOST_MESSAGE.format(
        host="testserver",
        allowed_hosts="[] (empty with DEBUG on, which allows only '.localhost', '127.0.0.1' and '[::1]')",
    )


def test_server_error_is_not_explained_as_disallowed_host():
    app = Flask("test_app")

    @app.route("/schema")
    def schema():
        return "", 500

    with pytest.raises(LoaderError) as exc:
        schemathesis.openapi.from_wsgi("/schema", app)
    assert str(exc.value) == "Failed to load schema due to server error (HTTP 500 Internal Server Error)"


def make_flask_bad_request_app():
    app = Flask("test_app")

    @app.route("/schema")
    def schema():
        return "", 400

    return app


@pytest.mark.parametrize("make_app", [get_wsgi_application, make_flask_bad_request_app], ids=["django", "flask"])
def test_client_error_unrelated_to_allowed_hosts(django_settings, make_app):
    with override_settings(ROOT_URLCONF=__name__, ALLOWED_HOSTS=["*"], DEBUG=False, MIDDLEWARE=[COMMON_MIDDLEWARE]):
        app = make_app()
        with pytest.raises(LoaderError) as exc:
            schemathesis.openapi.from_wsgi("/schema", app)
    assert str(exc.value) == "Failed to load schema due to client error (HTTP 400 Bad Request)"
