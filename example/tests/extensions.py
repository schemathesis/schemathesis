from datetime import timedelta

from hypothesis import strategies as st

import schemathesis


@st.composite
def fullname(draw):
    """Custom strategy for full names."""
    first = draw(st.sampled_from(["jonh", "jane"]))
    last = draw(st.just("doe"))
    return f"{first} {last}"


schemathesis.openapi.format("fullname", fullname())


@schemathesis.hook
def filter_body(ctx, body):
    """Modification over the default strategy for payload generation."""
    return body.get("id", 10001) > 10000


@schemathesis.check
def not_so_slow(ctx, response, case):
    """Custom response check."""
    assert response.elapsed < timedelta(milliseconds=100), "Response is slow!"


@schemathesis.target
def big_response(ctx):
    """Custom data generation target."""
    return float(len(ctx.response.content))
