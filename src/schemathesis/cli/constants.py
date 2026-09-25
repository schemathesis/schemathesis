from enum import IntEnum

MISSING_BASE_URL_MESSAGE = "The `--url` option is required when specifying a schema via a file."
MIN_WORKERS = 1
DEFAULT_WORKERS = MIN_WORKERS
MAX_WORKERS = 64
COLOR_OPTIONS_INVALID_USAGE_MESSAGE = "Can't use `--no-color` and `--force-color` simultaneously"
ISSUE_TRACKER_URL = (
    "https://github.com/schemathesis/schemathesis/issues/new?"
    "labels=Status%3A%20Needs%20Triage%2C+Type%3A+Bug&template=bug_report.md&title=%5BBUG%5D"
)
EXTENSIONS_DOCUMENTATION_URL = "https://schemathesis.readthedocs.io/en/stable/guides/extending/"


class ExitCode(IntEnum):
    OK = 0
    # Checks failed or bugs were found.
    FAILURES = 1
    # The run could not do its job: configuration, schema or internal error, or nothing was tested.
    ERROR = 2
    # Ctrl-C stopped the run before it finished, following the shell's 128 + SIGINT convention.
    INTERRUPTED = 130
