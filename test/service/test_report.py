import json
import os
import tarfile
from dataclasses import asdict
from io import BytesIO
from unittest import mock

import pytest

import schemathesis
from schemathesis.cli import ExecutionContext
from schemathesis.internal.datetime import current_datetime
from schemathesis.runner import events
from schemathesis.service import ci, metadata, report


def test_add_events(openapi3_schema_url, read_report):
    schema = schemathesis.from_uri(openapi3_schema_url, validate_schema=False)
    payload = BytesIO()
    with tarfile.open(mode="w:gz", fileobj=payload) as tar:
        writer = report.ReportWriter(tar)
        for event in schemathesis.runner.from_schema(schema).execute():
            writer.add_event(event)
    data = payload.getvalue()
    with read_report(data) as tar:
        members = tar.getmembers()
        assert len(members) == 10
        expected = (
            "Initialized",
            "BeforeProbing",
            "AfterProbing",
            "BeforeAnalysis",
            "AfterAnalysis",
            "BeforeExecution",
            "AfterExecution",
            "BeforeExecution",
            "AfterExecution",
            "Finished",
        )
        for event_type, member in zip(expected, members):
            event = json.load(tar.extractfile(member.name))
            assert event_type in event


def test_metadata(read_report):
    payload = BytesIO()
    with tarfile.open(mode="w:gz", fileobj=payload) as tar:
        writer = report.ReportWriter(tar)
        writer.add_metadata(
            api_name="test",
            location="http://127.0.0.1",
            base_url="http://127.0.0.1",
            metadata=metadata.Metadata(),
            started_at=current_datetime(),
            ci_environment=ci.environment(),
            usage_data=None,
        )
    data = payload.getvalue()
    with read_report(data) as tar:
        assert len(tar.getmembers()) == 1
        assert asdict(metadata.Metadata()) == json.load(tar.extractfile("metadata.json"))["environment"]


def generate_events(schema_url):
    schema = schemathesis.from_uri(schema_url, validate_schema=False)
    yield from schemathesis.runner.from_schema(schema).execute()


@pytest.mark.operations("success")
def test_do_not_send_incomplete_report_service(service_report_handler, service, openapi3_schema_url):
    # When the test process is interrupted or there is an internal error
    context = mock.create_autospec(ExecutionContext)
    for event in generate_events(openapi3_schema_url):
        if isinstance(event, events.Finished):
            service_report_handler.handle_event(context, events.Interrupted())
        else:
            service_report_handler.handle_event(context, event)
    # Then the report should not be sent
    assert not service.server.log


@pytest.mark.operations("success")
def test_do_not_send_incomplete_report_file(file_report_handler, service, openapi3_schema_url):
    # When the test process is interrupted or there is an internal error
    context = mock.create_autospec(ExecutionContext)
    for event in generate_events(openapi3_schema_url):
        if isinstance(event, events.Finished):
            file_report_handler.handle_event(context, events.Interrupted())
        else:
            file_report_handler.handle_event(context, event)
    file_report_handler.shutdown()
    # Then the report should not be sent
    assert not os.path.exists(file_report_handler.file_handle.name)
