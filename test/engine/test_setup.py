import time

from flask import request

import schemathesis
from schemathesis.engine import events
from schemathesis.engine.run import PhaseName
from test.apps.catalog.openapi.sign_up import sign_up_and_login
from test.utils import EventStream


def test_setup_runs_once_before_testing(ctx):
    api = ctx.openapi.apps.success()
    stream = EventStream(schemathesis.openapi.from_url(api.schema_url), phases=[PhaseName.FUZZING], max_examples=1)
    assert [event.phase.name for event in stream.execute().find_all(events.PhaseStarted)] == [
        PhaseName.SETUP,
        PhaseName.EXAMPLES,
        PhaseName.COVERAGE,
        PhaseName.FUZZING,
        PhaseName.STATEFUL_TESTING,
    ]


def test_setup_does_not_sign_up_after_the_time_budget_runs_out(ctx):
    app = sign_up_and_login()

    # Slow probes spend the whole budget before sign-up gets its turn.
    @app.server.before_request
    def delay_probes():
        if "X-Schemathesis-Probe" in request.headers:
            time.sleep(1)

    api = ctx.openapi.serve(app)
    EventStream(schemathesis.openapi.from_url(api.schema_url), phases=[PhaseName.FUZZING], max_time=1).execute()
    assert api.calls_to("/auth/register") == []
