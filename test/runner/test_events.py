from schemathesis.runner import events


def test_unknown_exception():
    try:
        raise ZeroDivisionError("division by zero")
    except ZeroDivisionError as exc:
        event = events.InternalError.from_exc(exc)
        assert event.message == "An internal error occurred during the test run"
        assert event.exception.strip() == "ZeroDivisionError: division by zero"
