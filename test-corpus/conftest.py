import json
import socket
import warnings

import pytest

LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def pytest_configure(config):
    warnings.filterwarnings("ignore", category=pytest.PytestDeprecationWarning)


def _is_loopback(address):
    # Anything that is not a host/port pair is a local channel (Unix socket, and so on).
    if not isinstance(address, tuple):
        return True
    return address[0] in LOOPBACK_HOSTS


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    # A schema reaching for a remote `$ref` must fail the same way on every machine, and the corpus
    # is pinned to a commit while the documents it points at are not.
    getaddrinfo = socket.getaddrinfo
    connect = socket.socket.connect

    def guarded_getaddrinfo(host, *args, **kwargs):
        if host not in LOOPBACK_HOSTS:
            raise OSError(f"Corpus tests run without network access, and something asked for {host!r}")
        return getaddrinfo(host, *args, **kwargs)

    def guarded_connect(self, address):
        if not _is_loopback(address):
            raise OSError(f"Corpus tests run without network access, and something asked for {address!r}")
        return connect(self, address)

    monkeypatch.setattr(socket, "getaddrinfo", guarded_getaddrinfo)
    monkeypatch.setattr(socket.socket, "connect", guarded_connect)


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    """Report `KNOWN_BODY_VIOLATIONS` entries that never fired so the list doesn't rot.

    Skipped under `-k` filters: partial runs naturally leave entries undrained.
    """
    if config.getoption("keyword"):
        return
    if not any(
        report.nodeid.startswith("test_corpus.py::test_coverage_phase")
        for report in terminalreporter.getreports("passed") + terminalreporter.getreports("failed")
    ):
        return
    try:
        from test_corpus import _PENDING_BODY_VIOLATIONS
    except ImportError:
        return
    if not _PENDING_BODY_VIOLATIONS:
        return
    terminalreporter.section("Stale KNOWN_BODY_VIOLATIONS entries", sep="-", red=True)
    terminalreporter.write_line(
        f"{len(_PENDING_BODY_VIOLATIONS)} entries did not fire — bodies are now valid; remove them:"
    )
    for schema_id, label in sorted(_PENDING_BODY_VIOLATIONS):
        terminalreporter.write_line(f"  - ({schema_id!r}, {label!r})")


def clean_schema(obj):
    # A helper to display schemas without fields that make too much noise and are irrelevant to dependency analysis
    if isinstance(obj, dict):
        return {k: clean_schema(v) for k, v in obj.items() if k not in ("description", "title", "summary")}
    elif isinstance(obj, list):
        return [clean_schema(item) for item in obj]
    else:
        return obj


@pytest.fixture
def save_schema():
    def save_schema(schema, filename="schema.json"):
        with open(filename, "w") as fd:
            json.dump(clean_schema(schema), fd, indent=4)

    return save_schema
