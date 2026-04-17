from __future__ import annotations

import base64
import dataclasses
import hashlib
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import jsonschema_rs
import pytest

from schemathesis.config import (
    JunitReportConfig,
    OutputConfig,
    ReportConfig,
    ReportFormat,
    ReportsConfig,
    SanitizationConfig,
)
from schemathesis.core.failures import Failure, Severity
from schemathesis.core.transport import Response

if TYPE_CHECKING:
    from xdist.workermanage import WorkerController

    from schemathesis.engine.recorder import ScenarioRecorder
    from schemathesis.generation.meta import CaseMetadata
    from schemathesis.reporting.allure import AllureWriter
    from schemathesis.reporting.har import HarWriter
    from schemathesis.reporting.junitxml import JunitXmlWriter
    from schemathesis.reporting.vcr import VcrWriter
    from schemathesis.schemas import BaseSchema

# schema_id -> {"writer_config": ..., "records": [...]}
_XDIST_WRITERS_KEY: pytest.StashKey[dict[str, dict]] = pytest.StashKey()
# Key used in xdist workeroutput to pass serialized recorders from workers to the controller
SCHEMATHESIS_RECORDERS_KEY = "schemathesis_recorders"


class _MimeProxy:
    """Carries a mime-type string when replaying serialized attachment calls.

    Satisfies the _HasMimeType protocol (runtime_checkable) checked inside
    AllureWriter.accumulate_attachment.
    """

    __slots__ = ("mime_type",)

    def __init__(self, mime_type: str) -> None:
        self.mime_type = mime_type


class _CaseMetaProxy:
    """Minimal case proxy for controller-side deserialized recorders.

    Holds only generation metadata - full Case objects carry APIOperation/schema
    references that are not suitable for cross-process serialization.
    """

    __slots__ = ("meta",)

    def __init__(self, meta: CaseMetadata | None) -> None:
        self.meta = meta


def serialize_recorder(
    recorder: ScenarioRecorder,
    elapsed_sec: float,
    tags: list[str] | None = None,
    allure_calls: list[dict] | None = None,
) -> dict:
    """Serialize a ScenarioRecorder for cross-process transport via workeroutput."""
    interactions: dict[str, dict] = {}
    for case_id, interaction in recorder.interactions.items():
        request = interaction.request
        response = interaction.response
        interactions[case_id] = {
            "timestamp": interaction.timestamp,
            "request": {
                "method": request.method,
                "uri": request.uri,
                "headers": request.headers,
                "body": {"$base64": base64.b64encode(request.body).decode()} if request.body is not None else None,
                "body_size": request.body_size,
            },
            "response": (
                {
                    "status_code": response.status_code,
                    "headers": response.headers,
                    "content": {"$base64": base64.b64encode(response.content).decode()},
                    "elapsed": response.elapsed,
                    "message": response.message,
                    "http_version": response.http_version,
                    "encoding": response.encoding,
                    "verify": response.verify,
                }
                if response is not None
                else None
            ),
        }

    checks: dict[str, list[dict]] = {}
    for case_id, check_list in recorder.checks.items():
        checks[case_id] = [
            {
                "name": check.name,
                "status": check.status.name,
                "failure_info": (
                    {
                        "code_sample": check.failure_info.code_sample,
                        "failure": {
                            "operation": check.failure_info.failure.operation,
                            "title": check.failure_info.failure.title,
                            "message": check.failure_info.failure.message,
                            "case_id": check.failure_info.failure.case_id,
                            "severity": check.failure_info.failure.severity.name,
                        },
                    }
                    if check.failure_info is not None
                    else None
                ),
            }
            for check in check_list
        ]

    cases: dict[str, dict | None] = {}
    for case_id, case_node in recorder.cases.items():
        meta = case_node.value.meta if case_node.value is not None else None
        cases[case_id] = meta.to_dict() if meta is not None else None

    serialized_calls: list[dict] = []
    for call in allure_calls or []:
        if call["type"] == "attach":
            serialized_calls.append(
                {
                    "type": "attach",
                    "label": call["label"],
                    "name": call["name"],
                    "body": {"$base64": base64.b64encode(call["body"]).decode()},
                    "mime": call["mime"],
                }
            )
        else:
            serialized_calls.append(call)

    return {
        "label": recorder.label,
        "elapsed_sec": elapsed_sec,
        "interactions": interactions,
        "checks": checks,
        "cases": cases,
        "tags": tags,
        "allure_calls": serialized_calls,
    }


def deserialize_recorder(data: dict) -> tuple[ScenarioRecorder, float]:
    """Reconstruct a ScenarioRecorder and elapsed time from serialized data."""
    import requests.structures

    from schemathesis.engine import Status
    from schemathesis.engine.recorder import (
        CaseNode,
        CheckFailureInfo,
        CheckNode,
        Interaction,
        Request,
        ScenarioRecorder,
    )
    from schemathesis.generation.meta import CaseMetadata

    recorder = ScenarioRecorder(label=data["label"])

    for case_id, interaction_data in data["interactions"].items():
        req_data = interaction_data["request"]
        body_raw = req_data["body"]
        body = base64.b64decode(body_raw["$base64"]) if body_raw is not None else None
        request = Request(
            method=req_data["method"],
            uri=req_data["uri"],
            headers=req_data["headers"],
            body=body,
            body_size=req_data["body_size"],
        )
        resp_data = interaction_data["response"]
        if resp_data is not None:
            prepared = requests.PreparedRequest()
            prepared.method = req_data["method"]
            prepared.url = req_data["uri"]
            prepared.headers = requests.structures.CaseInsensitiveDict(
                {k: v[0] for k, v in req_data["headers"].items()}
            )
            prepared.body = body
            response: Response | None = Response(
                status_code=resp_data["status_code"],
                headers=resp_data["headers"],
                content=base64.b64decode(resp_data["content"]["$base64"]),
                request=prepared,
                elapsed=resp_data["elapsed"],
                message=resp_data["message"],
                http_version=resp_data["http_version"],
                encoding=resp_data["encoding"],
                verify=resp_data["verify"],
            )
        else:
            response = None
        interaction = Interaction(request=request, response=response)
        interaction.timestamp = interaction_data["timestamp"]
        recorder.interactions[case_id] = interaction

    for case_id, checks in data["checks"].items():
        for check in checks:
            status = Status[check["status"]]
            info = check["failure_info"]
            if info is not None:
                failure_data = info["failure"]
                failure = Failure(
                    operation=failure_data["operation"],
                    title=failure_data["title"],
                    message=failure_data["message"],
                    case_id=failure_data["case_id"],
                    severity=Severity[failure_data["severity"]],
                )
                failure_info: CheckFailureInfo | None = CheckFailureInfo(
                    code_sample=info["code_sample"],
                    failure=failure,
                )
            else:
                failure_info = None
            recorder.checks.setdefault(case_id, []).append(
                CheckNode(name=check["name"], status=status, failure_info=failure_info)
            )

    for case_id, meta_data in data["cases"].items():
        meta = CaseMetadata.from_dict(meta_data) if meta_data is not None else None
        recorder.cases[case_id] = CaseNode(
            # proxy holds only metadata, not a full Case
            value=_CaseMetaProxy(meta),  # type: ignore[arg-type]
            parent_id=None,
            transition=None,
            is_transition_applied=False,
        )

    return recorder, data["elapsed_sec"]


def _schema_id(schema: BaseSchema) -> str:
    """Stable cross-process identifier for a schema instance.

    Uses the schema's source location when available; falls back to a canonical
    hash of the raw schema content for in-memory (from_dict) schemas.
    """
    source = schema.location or jsonschema_rs.canonical.json.to_string(schema.raw_schema)
    return hashlib.sha256(source.encode()).hexdigest()[:16]


def _serialize_writer_config(schema: BaseSchema) -> dict:
    """Serialize the minimal writer configuration for cross-process transport."""
    reports = schema.config.reports
    paths: dict[str, str | None] = {}
    for fmt in (ReportFormat.VCR, ReportFormat.HAR, ReportFormat.JUNIT, ReportFormat.ALLURE):
        report = getattr(reports, fmt.value)
        if report.enabled:
            paths[fmt.value] = str(report.path) if report.path is not None else None

    return {
        "seed": schema.config.seed,
        "command": " ".join(sys.argv),
        "preserve_bytes": reports.preserve_bytes,
        "sanitization": dataclasses.asdict(schema.config.output.sanitization),
        "directory": str(reports.directory),
        "paths": paths,
        "api_title": schema.raw_schema.get("info", {}).get("title"),
    }


def _open_writers_from_config(
    writer_config: dict, suffix: str | None = None
) -> list[VcrWriter | HarWriter | JunitXmlWriter | AllureWriter]:
    """Open report writers on the controller using serialized writer configuration."""
    from schemathesis.reporting.har import HarWriter
    from schemathesis.reporting.junitxml import JunitXmlWriter
    from schemathesis.reporting.vcr import VcrWriter

    raw = writer_config["sanitization"]
    output = OutputConfig(
        sanitization=SanitizationConfig(
            enabled=raw["enabled"],
            keys_to_sanitize=tuple(raw["keys_to_sanitize"]),
            sensitive_markers=tuple(raw["sensitive_markers"]),
            replacement=raw["replacement"],
        )
    )
    seed = writer_config["seed"]
    command = writer_config["command"]
    paths = writer_config["paths"]

    def _rc(fmt: ReportFormat) -> ReportConfig:
        explicit = paths.get(fmt.value)
        return ReportConfig(enabled=fmt.value in paths, path=Path(explicit) if explicit else None)

    reports = ReportsConfig(
        directory=writer_config["directory"],
        preserve_bytes=writer_config["preserve_bytes"],
        vcr=_rc(ReportFormat.VCR),
        har=_rc(ReportFormat.HAR),
        junit=JunitReportConfig(
            enabled=ReportFormat.JUNIT.value in paths,
            path=Path(paths[ReportFormat.JUNIT.value]) if paths.get(ReportFormat.JUNIT.value) else None,
        ),
        allure=_rc(ReportFormat.ALLURE),
    )

    writers: list[VcrWriter | HarWriter | JunitXmlWriter | AllureWriter] = []
    try:
        if ReportFormat.VCR.value in paths:
            vcr_writer = VcrWriter(
                output=reports.get_stable_path(ReportFormat.VCR, suffix=suffix),
                config=output,
                preserve_bytes=writer_config["preserve_bytes"],
            )
            vcr_writer.open(seed=seed, command=command)
            writers.append(vcr_writer)
        if ReportFormat.HAR.value in paths:
            har_writer = HarWriter(
                output=reports.get_stable_path(ReportFormat.HAR, suffix=suffix),
                config=output,
                preserve_bytes=writer_config["preserve_bytes"],
            )
            har_writer.open(seed=seed)
            writers.append(har_writer)
        if ReportFormat.JUNIT.value in paths:
            writers.append(
                JunitXmlWriter(output=reports.get_stable_path(ReportFormat.JUNIT, suffix=suffix), config=output)
            )
        if ReportFormat.ALLURE.value in paths:
            from schemathesis.reporting.allure import AllureWriter

            writers.append(
                AllureWriter(
                    output_dir=reports.get_stable_path(ReportFormat.ALLURE, suffix=suffix),
                    config=output,
                    api_title=writer_config.get("api_title"),
                )
            )
    except Exception:
        for writer in writers:
            writer.close()
        raise
    return writers


class XdistReportingPlugin:
    """Controller-side pytest plugin for xdist reporting."""

    def pytest_testnodedown(self, node: WorkerController, error: object) -> None:
        # Accumulate per schema_id; writing is deferred to pytest_sessionfinish
        # so we know the full set of schemas before opening any files.
        stash = node.config.stash.setdefault(_XDIST_WRITERS_KEY, {})
        for schema_id, payload in node.workeroutput.get(SCHEMATHESIS_RECORDERS_KEY, {}).items():
            if schema_id not in stash:
                stash[schema_id] = {"writer_config": payload["writer_config"], "records": []}
            stash[schema_id]["records"].extend(payload["records"])

    def pytest_sessionfinish(self, session: pytest.Session, exitstatus: int) -> None:
        try:
            from schemathesis.reporting.allure import AllureWriter
        except ImportError:
            AllureWriter = None  # type: ignore[assignment,misc]
        from schemathesis.reporting.junitxml import JunitXmlWriter

        if hasattr(session.config, "workerinput"):
            return
        data = session.config.stash.get(_XDIST_WRITERS_KEY, {})
        if not data:
            return

        multi = len(data) > 1
        for schema_id, payload in data.items():
            suffix = schema_id[:8] if multi else None
            writers = _open_writers_from_config(payload["writer_config"], suffix=suffix)
            try:
                for record in payload["records"]:
                    recorder, elapsed_sec = deserialize_recorder(record)
                    tags: list[str] | None = record.get("tags")
                    for writer in writers:
                        if isinstance(writer, JunitXmlWriter):
                            writer.write(recorder, elapsed_sec)
                        elif AllureWriter is not None and isinstance(writer, AllureWriter):
                            writer.write(recorder, elapsed_sec, tags=tags)
                            for call in record.get("allure_calls", []):
                                t = call["type"]
                                label = call["label"]
                                if t == "attach":
                                    body = base64.b64decode(call["body"]["$base64"])
                                    writer.accumulate_attachment(label, call["name"], body, _MimeProxy(call["mime"]))
                                elif t == "link":
                                    writer.accumulate_link(label, call["url"], call["link_type"], call["name"])
                                elif t == "title":
                                    writer.accumulate_title(label, call["title"])
                                elif t == "description":
                                    writer.accumulate_description(label, call["description"])
                        else:
                            writer.write(recorder)
            finally:
                for writer in writers:
                    writer.close()
