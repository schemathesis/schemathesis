import pytest

from schemathesis.core.failures import FailureGroup
from schemathesis.core.transport import Response
from schemathesis.engine._validate import validate_response
from schemathesis.engine.recorder import ScenarioRecorder
from test.utils import check_context, oas3_response


def test_deduplication(ctx, response_factory):
    # See GH-1394
    operation = ctx.openapi.load_operation(
        "GET /data",
        {
            "responses": {"200": oas3_response({"type": "integer"}, description="OK")},
        },
    )
    case = operation.Case()
    response = Response.from_requests(response_factory.requests(), True)
    recorder = ScenarioRecorder(label="test")
    recorder.record_case(parent_id=None, case=case, transition=None, is_transition_applied=False)
    recorder.record_response(case_id=case.id, response=response)
    # When there are two checks that raise the same failure
    with pytest.raises(FailureGroup):
        validate_response(
            case=case,
            ctx=check_context(),
            recorder=recorder,
            response=response,
            continue_on_failure=False,
        )
    # Then the resulting output should be deduplicated
    assert (
        len([check for checks in recorder.checks.values() for check in checks if check.failure_info is not None]) == 1
    )


@pytest.mark.parametrize("referenced", [True, False], ids=["referenced", "unreferenced"])
def test_iter_chain_cases_includes_earlier_sibling(ctx, referenced):
    # An earlier write that used the created resource may have changed what the failing step sees; a read did not.
    schema = ctx.openapi.load_schema(
        {
            "/users": {
                "post": {
                    "requestBody": {"content": {"application/json": {"schema": {"type": "object"}}}},
                    "responses": {"201": {"description": "Created"}},
                },
            },
            "/users/{userId}": {
                "get": {
                    "parameters": [{"name": "userId", "in": "path", "required": True, "schema": {"type": "integer"}}],
                    "responses": {"200": {"description": "OK"}},
                },
                "delete": {
                    "parameters": [{"name": "userId", "in": "path", "required": True, "schema": {"type": "integer"}}],
                    "responses": {"204": {"description": "No Content"}},
                },
            },
        }
    )
    post_op = schema["/users"]["POST"]
    get_op = schema["/users/{userId}"]["GET"]
    delete_op = schema["/users/{userId}"]["DELETE"]

    parent = post_op.Case(method="POST", body={}, media_type="application/json")
    other_root = post_op.Case(method="POST", body={}, media_type="application/json")
    sibling_delete = delete_op.Case(method="DELETE", path_parameters={"userId": 1})
    unlinked_delete = delete_op.Case(method="DELETE", path_parameters={"userId": 2})
    probe = post_op.Case(method="POST", body={}, media_type="application/json")
    sibling_get = get_op.Case(method="GET", path_parameters={"userId": 1})
    current_get = get_op.Case(method="GET", path_parameters={"userId": 1})
    later_delete = delete_op.Case(method="DELETE", path_parameters={"userId": 1})

    recorder = ScenarioRecorder(label="chain-helper-test")
    recorder.record_case(case=parent)
    recorder.record_case(case=other_root)
    recorder.record_case(parent_id=parent.id, case=sibling_delete, is_transition_applied=True)
    recorder.record_case(parent_id=parent.id, case=unlinked_delete)
    recorder.record_case(parent_id=parent.id, case=probe, is_transition_applied=True, is_probe=True)
    recorder.record_case(parent_id=parent.id, case=sibling_get, is_transition_applied=True)
    recorder.record_case(parent_id=parent.id, case=current_get, is_transition_applied=True)
    recorder.record_case(parent_id=parent.id, case=later_delete, is_transition_applied=True)

    related_case_ids = (sibling_delete.id,) if referenced else ()
    chain = list(recorder.iter_chain_cases(case_id=current_get.id, related_case_ids=related_case_ids))
    assert [c.id for c in chain] == [parent.id, sibling_delete.id, current_get.id]
