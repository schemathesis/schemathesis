import platform
import threading
import time

import pytest
import requests

import schemathesis
from schemathesis.config import SchemathesisConfig
from schemathesis.core.transport import Response
from schemathesis.engine.health import HealthState
from schemathesis.engine.outage import CONFIRMATION_INTERVAL, REPORTED_REQUESTS, SERVER_LABEL, ServerMonitor
from schemathesis.engine.run import PhaseName
from test.utils import refusing_url

# Connecting to an unused loopback port times out on Windows instead of being refused, so there is no
# refusal for the monitor to classify either way.
pytestmark = pytest.mark.skipif(platform.system() == "Windows", reason="Unused ports do not refuse connections")


@pytest.fixture
def operation(ctx):
    schema = ctx.openapi.load_schema({"/users": {"get": {"responses": {"200": {"description": "OK"}}}}})
    with refusing_url() as url:
        schema.config.update(base_url=url)
        yield schema["/users"]["get"]


def answer(case, elapsed=0.1):
    request = requests.Request("GET", f"{case.operation.base_url}/users")
    return Response(status_code=200, headers={}, content=b"", request=request, elapsed=elapsed, verify=True)


def time_out(case):
    raise requests.ReadTimeout("Read timed out", request=requests.Request("GET", f"{case.operation.base_url}/users"))


def refusal(base_url):
    with pytest.raises(requests.ConnectionError) as exc:
        requests.get(f"{base_url}/users", timeout=1)
    return exc.value


def expected_message(operation, cases):
    noun = "request" if len(cases) == 1 else "requests"
    commands = "\n\n".join(f"    {case.as_curl_command(headers={}, verify=True)}" for case in cases)
    return f"{operation.base_url} stopped responding. Last {noun} before it went away:\n\n{commands}"


def hang(monitor, case, release):
    """Start a request that stays in flight until `release` is set; returns once the monitor knows about it."""
    entered = threading.Event()

    def send():
        entered.set()
        release.wait(timeout=5)
        return answer(case)

    worker = threading.Thread(target=lambda: monitor.track(case, send, transport_kwargs={}))
    worker.start()
    assert entered.wait(timeout=5)
    return worker


def test_outage_lists_the_newest_requests_in_send_order(operation):
    monitor = ServerMonitor()
    cases = [operation.Case(query={"q": str(index)}) for index in range(REPORTED_REQUESTS + 2)]
    for case in cases:
        monitor.track(case, lambda case=case: answer(case), transport_kwargs={})

    assert monitor.is_down(refusal(operation.base_url))

    report = monitor.take_report(PhaseName.FUZZING)
    assert (report.label, report.related_to_operation, report.info.title) == (SERVER_LABEL, False, "Server Unavailable")
    assert report.info.message == expected_message(operation, list(reversed(cases))[:REPORTED_REQUESTS])


def test_outage_lists_a_request_that_never_came_back(operation):
    monitor = ServerMonitor()
    answered = operation.Case(query={"q": "answered"})
    monitor.track(answered, lambda: answer(answered), transport_kwargs={})

    hanging = operation.Case(query={"q": "hanging"})
    release = threading.Event()
    worker = hang(monitor, hanging, release)
    try:
        assert monitor.is_down(refusal(operation.base_url))
        assert monitor.take_report(PhaseName.FUZZING).info.message == expected_message(operation, [hanging, answered])
    finally:
        release.set()
        worker.join()


def test_workers_left_hanging_by_the_outage_do_not_hide_the_answered_culprit(operation):
    monitor = ServerMonitor()
    culprit = operation.Case(query={"q": "culprit"})
    monitor.track(culprit, lambda: answer(culprit), transport_kwargs={})

    release = threading.Event()
    hanging = [operation.Case(query={"q": f"hanging-{index}"}) for index in range(REPORTED_REQUESTS)]
    workers = [hang(monitor, case, release) for case in hanging]
    try:
        assert monitor.is_down(refusal(operation.base_url))
        assert monitor.take_report(PhaseName.FUZZING).info.message == expected_message(
            operation, [hanging[-1], culprit]
        )
    finally:
        release.set()
        for worker in workers:
            worker.join()


def drop(case):
    request = requests.Request("GET", f"{case.operation.base_url}/users").prepare()
    raise requests.ConnectionError(ConnectionResetError("Connection reset by peer"), request=request)


def test_call_dropped_by_the_crash_leads_calls_left_hanging_and_dropped_after_it(operation):
    monitor = ServerMonitor()
    answered = operation.Case(query={"q": "answered"})
    monitor.track(answered, lambda: answer(answered), transport_kwargs={})
    hanging = operation.Case(query={"q": "hanging"})
    release = threading.Event()
    worker = hang(monitor, hanging, release)
    crash = operation.Case(query={"q": "crash"})
    later = operation.Case(query={"q": "later"})
    try:
        with pytest.raises(requests.ConnectionError) as exc_info:
            monitor.track(crash, lambda: drop(crash), transport_kwargs={})
        with pytest.raises(requests.ConnectionError):
            monitor.track(later, lambda: drop(later), transport_kwargs={})

        assert monitor.is_down(exc_info.value)
        assert monitor.take_report(PhaseName.FUZZING).info.message == expected_message(
            operation, [crash, hanging, later, answered]
        )
    finally:
        release.set()
        worker.join()


def test_workers_refused_after_the_verdict_reuse_it_instead_of_probing_again(operation):
    monitor = ServerMonitor()
    first = operation.Case(query={"q": "first"})
    monitor.track(first, lambda: answer(first), transport_kwargs={})
    assert monitor.is_down(refusal(operation.base_url))

    later = operation.Case(query={"q": "later"})
    monitor.track(later, lambda: answer(later), transport_kwargs={})
    refused_again = refusal(operation.base_url)
    started = time.monotonic()
    assert monitor.is_down(refused_again)

    assert time.monotonic() - started < CONFIRMATION_INTERVAL
    assert monitor.take_report(PhaseName.FUZZING).info.message == expected_message(operation, [first])


def test_refusal_from_another_address_is_not_an_outage(operation):
    monitor = ServerMonitor()
    case = operation.Case()
    monitor.track(case, lambda: answer(case), transport_kwargs={})

    with refusing_url() as other:
        assert not monitor.is_down(refusal(other))
    assert monitor.take_report(PhaseName.FUZZING) is None


def test_requests_to_an_app_are_never_tracked(ctx):
    # An app is called in-process, so it has no host that could start refusing connections.
    app, _ = ctx.openapi.make_flask_app({"/users": {"get": {"responses": {"200": {"description": "OK"}}}}})
    operation = schemathesis.openapi.from_wsgi("/openapi.json", app)["/users"]["GET"]
    monitor = ServerMonitor()
    case = operation.Case()
    monitor.track(case, lambda: answer(case), transport_kwargs={})

    with refusing_url() as other:
        assert not monitor.is_down(refusal(other))


def test_refusal_from_a_server_never_reached_is_not_an_outage(operation):
    assert not ServerMonitor().is_down(refusal(operation.base_url))


def test_a_request_that_never_reached_the_server_does_not_prove_it_was_up(operation):
    monitor = ServerMonitor()
    case = operation.Case()

    def send():
        raise refusal(operation.base_url)

    with pytest.raises(requests.ConnectionError):
        monitor.track(case, send, transport_kwargs={})

    assert not monitor.is_down(refusal(operation.base_url))


def test_tracked_answers_and_timeouts_set_the_operation_timeout(operation):
    health = HealthState()
    monitor = ServerMonitor(health=health)
    case = operation.Case()
    monitor.track(case, lambda: answer(case, elapsed=0.5), transport_kwargs={})
    for _ in range(2):
        with pytest.raises(requests.ReadTimeout):
            monitor.track(case, lambda: time_out(case), transport_kwargs={})

    assert health.timeout_override(operation.label) == 2.0


@pytest.mark.parametrize("tls", [False, True], ids=["plaintext", "tls"])
def test_grpc_server_that_stops_is_an_outage(ctx, tls):
    pytest.importorskip("grpc", reason="grpcio has no free-threaded build")
    server = ctx.grpc.apps.users(tls=tls)
    config = {"base-url": server.target, **({"tls-verify": str(server.ca_path)} if tls else {})}
    schema = schemathesis.grpc.from_path(server.descriptor_set_path, config=SchemathesisConfig.from_dict(config))
    case = schema["pkg.Users"]["GetUser"].Case(body={"userId": 7})
    monitor = ServerMonitor()
    monitor.track(case, case.call, transport_kwargs={})
    server.stop()

    with pytest.raises(requests.ConnectionError) as exc_info:
        monitor.track(case, case.call, transport_kwargs={})

    assert monitor.is_down(exc_info.value)
    info = monitor.take_report(PhaseName.FUZZING).info
    assert (info.message, info.suggestion()) == (
        f"{server.target} stopped responding. Last request before it went away:\n\n"
        f"    {case.as_reproduction_command(headers={}, verify=True)}",
        # HAR reports do not record gRPC calls.
        "If the server crashed, the request that caused it may be older than these.",
    )


def crashing_server(ctx, how="exit"):
    pytest.importorskip("grpc", reason="grpcio has no free-threaded build")
    return ctx.grpc.apps.crashing_users(how)


def grpc_operation(server, **config):
    config = SchemathesisConfig.from_dict({"base-url": server.target, **config})
    return schemathesis.grpc.from_path(server.descriptor_set_path, config=config)["pkg.Users"]["GetUser"]


def grpc_message(server, cases):
    commands = "\n\n".join(f"    {case.as_reproduction_command(headers={}, verify=True)}" for case in cases)
    return f"{server.target} stopped responding. Last requests before it went away:\n\n{commands}"


# The culprit is listed, so no tip points to older requests.
@pytest.mark.parametrize("how", ["exit", "stop"], ids=["connection-dropped", "calls-cancelled"])
def test_grpc_call_that_crashes_the_server_is_listed_in_the_outage(ctx, how):
    server = crashing_server(ctx, how)
    operation = grpc_operation(server)
    answered = operation.Case(body={"userId": 7})
    crash = operation.Case(body={"userId": 500})
    monitor = ServerMonitor()
    monitor.track(answered, answered.call, transport_kwargs={})

    with pytest.raises(requests.ConnectionError) as exc_info:
        monitor.track(crash, crash.call, transport_kwargs={})

    assert monitor.is_down(exc_info.value)
    info = monitor.take_report(PhaseName.FUZZING).info
    assert (info.message, info.suggestion()) == (grpc_message(server, [crash, answered]), None)


def test_grpc_call_that_timed_out_before_the_crash_is_not_its_culprit(ctx):
    server = crashing_server(ctx)
    operation = grpc_operation(server)
    answered = operation.Case(body={"userId": 7})
    slow = operation.Case(body={"userId": 13})
    crash = operation.Case(body={"userId": 500})
    monitor = ServerMonitor()
    monitor.track(answered, answered.call, transport_kwargs={})
    with pytest.raises(requests.ReadTimeout):
        monitor.track(slow, lambda: slow.call(timeout=0.2), transport_kwargs={})

    with pytest.raises(requests.ConnectionError) as exc_info:
        monitor.track(crash, crash.call, transport_kwargs={})

    assert monitor.is_down(exc_info.value)
    assert monitor.take_report(PhaseName.FUZZING).info.message == grpc_message(server, [crash, answered])


# The call only cut off by the crash confirms the outage first, yet the culprit alone is an error of its own.
def test_grpc_calls_dropped_together_by_a_crash_list_the_latest_first_and_only_it_as_an_operation_error(ctx):
    server = crashing_server(ctx)
    operation = grpc_operation(server)
    answered = operation.Case(body={"userId": 7})
    slow = operation.Case(body={"userId": 13})
    crash = operation.Case(body={"userId": 500})
    monitor = ServerMonitor()
    monitor.track(answered, answered.call, transport_kwargs={})
    slow_errors = []

    def call_slow():
        with pytest.raises(requests.ConnectionError) as slow_info:
            monitor.track(slow, slow.call, transport_kwargs={})
        slow_errors.append(slow_info.value)

    worker = threading.Thread(target=call_slow)
    worker.start()
    server.wait_for_call(13)
    with pytest.raises(requests.ConnectionError) as exc_info:
        monitor.track(crash, crash.call, transport_kwargs={})
    worker.join()

    assert monitor.is_down(slow_errors[0])
    assert monitor.is_down(exc_info.value)
    assert (monitor.is_after_outage(slow_errors[0]), monitor.is_after_outage(exc_info.value)) == (True, False)
    assert monitor.take_report(PhaseName.FUZZING).info.message == grpc_message(server, [crash, slow, answered])


def test_live_grpc_tls_server_after_a_dropped_call_is_not_an_outage(ctx):
    pytest.importorskip("grpc", reason="grpcio has no free-threaded build")
    server = ctx.grpc.apps.users(tls=True)
    operation = grpc_operation(server, **{"tls-verify": str(server.ca_path)})
    case = operation.Case(body={"userId": 7})
    monitor = ServerMonitor()
    monitor.track(case, case.call, transport_kwargs={})
    request = requests.Request("POST", f"{server.target}{operation.path}").prepare()
    dropped = requests.ConnectionError(ConnectionAbortedError("Connection aborted. Socket closed"), request=request)

    assert not monitor.is_down(dropped)


@pytest.mark.parametrize(
    ("variable", "is_down"),
    [("ALL_PROXY", True), ("grpc_proxy", False)],
    ids=["ignored-by-grpc", "used-by-grpc"],
)
def test_grpc_outage_follows_grpc_proxy_variables(ctx, monkeypatch, variable, is_down):
    server = crashing_server(ctx)
    operation = grpc_operation(server)
    answered = operation.Case(body={"userId": 7})
    crash = operation.Case(body={"userId": 500})
    monitor = ServerMonitor()
    monitor.track(answered, answered.call, transport_kwargs={})
    monkeypatch.setenv(variable, "http://127.0.0.1:1")

    with pytest.raises(requests.ConnectionError) as exc_info:
        monitor.track(crash, crash.call, transport_kwargs={})

    assert monitor.is_down(exc_info.value) is is_down
