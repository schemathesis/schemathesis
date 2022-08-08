import os
import pathlib

IS_CI = os.getenv("CI") == "true"

DEFAULT_HOSTNAME = "api.schemathesis.io"
# The main Schemathesis.io API address
DEFAULT_URL = f"https://{DEFAULT_HOSTNAME}/"
DEFAULT_PROTOCOL = "https"
# An HTTP header name to store report correlation id
REPORT_CORRELATION_ID_HEADER = "X-Schemathesis-Report-Correlation-Id"
CI_PROVIDER_HEADER = "X-Schemathesis-CI-Provider"
# A sentinel to signal the worker thread to stop
STOP_MARKER = object()
# Timeout for each API call
REQUEST_TIMEOUT = 1
# The time the main thread will wait for the worker thread to finish its job before exiting
WORKER_FINISH_TIMEOUT = 10.0
# A period between checking the worker thread for events
# Decrease the frequency for CI environment to avoid too much output from the waiting spinner
WORKER_CHECK_PERIOD = 0.1 if IS_CI else 0.005
# Wait until the worker terminates
WORKER_JOIN_TIMEOUT = 10
# Version of the hosts file format
HOSTS_FORMAT_VERSION = "0.1"
# Upload report version
REPORT_FORMAT_VERSION = "1"
# Default path to the hosts file
DEFAULT_HOSTS_PATH = pathlib.Path.home() / ".config/schemathesis/hosts.toml"
TOKEN_ENV_VAR = "SCHEMATHESIS_TOKEN"
HOSTNAME_ENV_VAR = "SCHEMATHESIS_HOSTNAME"
PROTOCOL_ENV_VAR = "SCHEMATHESIS_PROTOCOL"
HOSTS_PATH_ENV_VAR = "SCHEMATHESIS_HOSTS_PATH"
URL_ENV_VAR = "SCHEMATHESIS_URL"
REPORT_ENV_VAR = "SCHEMATHESIS_REPORT"
TELEMETRY_ENV_VAR = "SCHEMATHESIS_TELEMETRY"
