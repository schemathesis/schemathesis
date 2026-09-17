import platform
import threading
import time

import pytest
import requests

import schemathesis
from schemathesis.core.transport import Response
from schemathesis.engine.outage import CONFIRMATION_INTERVAL, REPORTED_REQUESTS, SERVER_LABEL, ServerMonitor
from schemathesis.engine.run import PhaseName

# Connecting to an unused loopback port times out on Windows instead of being refused, so there is no
# refusal for the monitor to classify either way.
pytestmark = pytest.mark.skipif(platform.system() == "Windows", reason="Unused ports do not refuse connections")


@pytest.fixture
def operation(ctx, app_runner):
    schema = ctx.openapi.load_schema({"/users": {"get": {"responses": {"200": {"description": "OK"}}}}})
    schema.config.update(base_url=f"http://127.0.0.1:{app_runner.unused_port()}")
    return schema["/users"]["get"]


def answer(case):
    request = requests.Request("GET", f"{case.operation.base_url}/users")
    return Response(status_code=200, headers={}, content=b"", request=request, elapsed=0.1, verify=True)


def refusal(base_url):
    with pytest.raises(requests.ConnectionError) as exc:
        requests.get(f"{base_url}/users", timeout=1)
    return exc.value


def expected_message(operation, cases):
    noun = "request" if len(cases) == 1 else "requests"
    commands = "\n\n".join(f"    {case.as_curl_command(headers={}, verify=True)}" for case in cases)
    return f"{operation.base_url} stopped accepting connections. Last {noun} before it went away:\n\n{commands}"


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


def test_refusal_from_another_address_is_not_an_outage(operation, app_runner):
    monitor = ServerMonitor()
    case = operation.Case()
    monitor.track(case, lambda: answer(case), transport_kwargs={})

    assert not monitor.is_down(refusal(f"http://127.0.0.1:{app_runner.unused_port()}"))
    assert monitor.take_report(PhaseName.FUZZING) is None


def test_requests_to_an_app_are_never_tracked(ctx, app_runner):
    # An app is called in-process, so it has no host that could start refusing connections.
    app, _ = ctx.openapi.make_flask_app({"/users": {"get": {"responses": {"200": {"description": "OK"}}}}})
    operation = schemathesis.openapi.from_wsgi("/openapi.json", app)["/users"]["GET"]
    monitor = ServerMonitor()
    case = operation.Case()
    monitor.track(case, lambda: answer(case), transport_kwargs={})

    assert not monitor.is_down(refusal(f"http://127.0.0.1:{app_runner.unused_port()}"))


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
