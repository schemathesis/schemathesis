from hypothesis import given
from hypothesis import strategies as st

from schemathesis.cli.reporting import group_by_case
from schemathesis.code_samples import CodeSampleStyle
from schemathesis.models import Request, Response
from schemathesis.runner.serialization import SerializedCase, SerializedCheck, Status


@given(
    st.lists(
        st.builds(
            SerializedCheck,
            name=st.sampled_from(["first", "second", "third"]),
            value=st.just(Status.failure),
            request=st.builds(
                Request,
                method=st.just("GET"),
                uri=st.just("http://127.0.0.1/"),
                body=st.none(),
                body_size=st.just(0),
                headers=st.just({}),
            ),
            response=st.builds(
                Response,
                status_code=st.integers(min_value=200, max_value=599),
                message=st.just("Message"),
                headers=st.just({}),
                body=st.text() | st.none(),
                body_size=st.just(0),
                encoding=st.none(),
                http_version=st.just("1.1"),
                elapsed=st.just(1.0),
                verify=st.just(True),
            )
            | st.none(),
            example=st.just(
                SerializedCase(
                    id="testid",
                    generation_time=0.0,
                    path_parameters={},
                    headers={},
                    cookies={},
                    query={},
                    body=None,
                    media_type=None,
                    data_generation_method="N",
                    method="GET",
                    url="http://127.0.0.1/",
                    path_template="/",
                    transition_id=None,
                    full_path="/",
                    verbose_name="GET /",
                    verify=True,
                    extra_headers={},
                )
            ),
            context=st.none(),
            history=st.just([]),
        ),
    )
)
def test_group_by_case(checks):
    list(group_by_case(checks, CodeSampleStyle.curl))
