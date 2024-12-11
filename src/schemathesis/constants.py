SCHEMATHESIS_TEST_CASE_HEADER = "X-Schemathesis-TestCaseId"
HYPOTHESIS_IN_MEMORY_DATABASE_IDENTIFIER = ":memory:"
DISCORD_LINK = "https://discord.gg/R9ASRAmHnA"
GITHUB_APP_LINK = "https://github.com/apps/schemathesis"
DEFAULT_RESPONSE_TIMEOUT = 10
RECURSIVE_REFERENCE_ERROR_MESSAGE = (
    "Currently, Schemathesis can't generate data for this operation due to "
    "recursive references in the operation definition. See more information in "
    "this issue - https://github.com/schemathesis/schemathesis/issues/947"
)
SERIALIZERS_SUGGESTION_MESSAGE = (
    "You can register your own serializer with `schemathesis.serializer` "
    "and Schemathesis will be able to make API calls with this media type. \n"
    "See https://schemathesis.readthedocs.io/en/stable/how.html#payload-serialization for more information."
)
NO_LINKS_ERROR_MESSAGE = (
    "Stateful testing requires at least one OpenAPI link in the schema, but no links detected. "
    "Please add OpenAPI links to enable stateful testing or use stateless tests instead. \n"
    "See https://schemathesis.readthedocs.io/en/stable/stateful.html#how-to-specify-connections for more information."
)
EXTENSIONS_DOCUMENTATION_URL = "https://schemathesis.readthedocs.io/en/stable/extending.html"
ISSUE_TRACKER_URL = (
    "https://github.com/schemathesis/schemathesis/issues/new?"
    "labels=Status%3A%20Needs%20Triage%2C+Type%3A+Bug&template=bug_report.md&title=%5BBUG%5D"
)
FLAKY_FAILURE_MESSAGE = "[FLAKY] Schemathesis was not able to reliably reproduce this failure"
HOOKS_MODULE_ENV_VAR = "SCHEMATHESIS_HOOKS"
API_NAME_ENV_VAR = "SCHEMATHESIS_API_NAME"
BASE_URL_ENV_VAR = "SCHEMATHESIS_BASE_URL"
WAIT_FOR_SCHEMA_ENV_VAR = "SCHEMATHESIS_WAIT_FOR_SCHEMA"
REPORT_SUGGESTION_ENV_VAR = "SCHEMATHESIS_REPORT_SUGGESTION"
