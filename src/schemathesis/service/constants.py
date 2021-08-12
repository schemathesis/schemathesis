# The main Schemathesis.io API address
DEFAULT_URL = "https://api.schemathesis.io/"
# A sentinel to signal the worker thread to stop
STOP_MARKER = object()
# Timeout for each API call
REQUEST_TIMEOUT = 1
# The time the main thread will wait for the worker thread to finish its job before exiting
WORKER_FINISH_TIMEOUT = 10.0
# A period between checking the worker thread for events
WORKER_CHECK_PERIOD = 0.005
# Wait until the worker terminates
WORKER_JOIN_TIMEOUT = 10
