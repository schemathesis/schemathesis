import pathlib
import re

import pytest

from test.apps.catalog.grpc.descriptors import users_descriptor_set, write_descriptor_set

# Imported before `testdir` snapshots modules, so a run does not unload it for later tests.
pytest.importorskip("grpc", reason="grpcio has no free-threaded build")


def test_grpc_html_report_via_xdist(testdir, ctx):
    server = ctx.grpc.apps.users()
    path = write_descriptor_set(pathlib.Path(testdir.tmpdir), users_descriptor_set())
    report_dir = testdir.tmpdir.join("report")
    testdir.makepyfile(
        f"""
import schemathesis
from hypothesis import settings

def always_fails(ctx, response, case):
    raise AssertionError("rejected")

schema = schemathesis.grpc.from_path(r"{path}")
schema.config.update(base_url="{server.target}")
schema.config.reports.update(html_path=r"{report_dir}")

@schema.include(name="pkg.Users/GetUser").parametrize()
@settings(max_examples=1)
def test_api(case):
    case.call_and_validate(checks=[always_fails])
"""
    )
    testdir.runpytest("-n", "2").assert_outcomes(failed=1)
    html = report_dir.join("index.html").read_text(encoding="utf-8")
    # Workers decode the body; the controller only receives bytes.
    assert (
        re.findall(r'<span class="ev-label">Response</span>(.*?)</div>', html),
        re.findall(r'<pre class="body">(.*?)</pre>', html, flags=re.DOTALL),
    ) == (['<span class="status s2xx">OK</span>'], ["{\n  &quot;name&quot;: &quot;u2147483647&quot;\n}"])
