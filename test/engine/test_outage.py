import threading

import pytest
import requests

from schemathesis.engine.outage import SERVER_LABEL, ServerMonitor
from schemathesis.engine.run import PhaseName


def test_outage_lists_the_last_requests_of_the_most_recent_workers(case_factory, app_runner):
    port = app_runner.unused_port()
    url = f"http://127.0.0.1:{port}/users"
    monitor = ServerMonitor()

    def work(name, *cases):
        def send():
            for case in cases:
                monitor.record(case, requests.Request("GET", url), transport_kwargs={})

        thread = threading.Thread(target=send, name=name)
        thread.start()
        thread.join()

    stale = case_factory(query={"q": "stale"})
    busy = [case_factory(query={"q": f"busy-{index}"}) for index in range(6)]
    other = case_factory(query={"q": "other"})
    work("worker-0", stale)
    work("worker-1", *busy)
    work("worker-2", other)
    with pytest.raises(requests.ConnectionError) as refused:
        requests.get(url, timeout=1)

    assert monitor.is_down(refused.value, workers=2)
    report = monitor.take_report(PhaseName.FUZZING)
    assert (report.label, report.related_to_operation, report.info.title) == (SERVER_LABEL, False, "Server Unavailable")
    assert report.info.message == (
        f"http://127.0.0.1:{port} stopped accepting connections. Last requests before it went away:\n\n"
        + "\n\n".join(f"    {case.as_curl_command(headers={}, verify=True)}" for case in [other, *reversed(busy[1:])])
    )
    assert monitor.take_report(PhaseName.FUZZING) is None


def test_refusal_from_a_server_never_reached_is_not_an_outage(app_runner):
    with pytest.raises(requests.ConnectionError) as refused:
        requests.get(f"http://127.0.0.1:{app_runner.unused_port()}/users", timeout=1)

    assert not ServerMonitor().is_down(refused.value, workers=1)
