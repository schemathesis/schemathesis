import json
import tarfile
from contextlib import contextmanager
from io import BytesIO
from queue import Queue
from unittest import mock

import attr
import pytest

import schemathesis
from schemathesis.cli import ExecutionContext
from schemathesis.runner import events
from schemathesis.service import metadata, report


@contextmanager
def read_report(data):
    buffer = BytesIO()
    buffer.write(data)
    buffer.seek(0)
    with tarfile.open(mode="r:gz", fileobj=buffer) as tar:
        yield tar


def test_add_events(openapi3_schema_url):
    schema = schemathesis.from_uri(openapi3_schema_url, validate_schema=False)
    payload = BytesIO()
    with tarfile.open(mode="w:gz", fileobj=payload) as tar:
        writer = report.ReportWriter(tar)
        for event in schemathesis.runner.from_schema(schema).execute():
            writer.add_event(event)
    data = payload.getvalue()
    with read_report(data) as tar:
        members = tar.getmembers()
        assert len(members) == 6
        expected = (
            "Initialized",
            "BeforeExecution",
            "AfterExecution",
            "BeforeExecution",
            "AfterExecution",
            "Finished",
        )
        for event_type, member in zip(expected, members):
            event = json.load(tar.extractfile(member.name))
            assert event_type in event


def test_metadata():
    payload = BytesIO()
    with tarfile.open(mode="w:gz", fileobj=payload) as tar:
        writer = report.ReportWriter(tar)
        writer.add_metadata(
            api_name="test", location="http://127.0.0.1", base_url="http://127.0.0.1", metadata=metadata.Metadata()
        )
    data = payload.getvalue()
    with read_report(data) as tar:
        assert len(tar.getmembers()) == 1
        assert attr.asdict(metadata.Metadata()) == json.load(tar.extractfile("metadata.json"))["environment"]


@pytest.mark.operations("success")
def test_do_not_send_incomplete_report(report_handler, service, openapi3_schema_url):
    # When the test process is interrupted or there is an internal error
    schema = schemathesis.from_uri(openapi3_schema_url, validate_schema=False)
    context = mock.create_autospec(ExecutionContext)
    for event in schemathesis.runner.from_schema(schema).execute():
        if isinstance(event, events.Finished):
            report_handler.handle_event(context, events.Interrupted())
        else:
            report_handler.handle_event(context, event)
    # Then the report should not be sent
    assert not service.server.log
