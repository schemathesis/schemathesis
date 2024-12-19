from __future__ import annotations

SCHEMATHESIS_TEST_CASE_HEADER = "X-Schemathesis-TestCaseId"


class NotSet:
    pass


NOT_SET = NotSet()


def string_to_boolean(value: str) -> str | bool:
    if value.lower() in ("y", "yes", "t", "true", "on", "1"):
        return True
    if value.lower() in ("n", "no", "f", "false", "off", "0"):
        return False
    return value
