from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

import schemathesis
from schemathesis.core.failures import Failure
from schemathesis.core.output.sanitization import sanitize_value
from schemathesis.core.parameters import CONTAINER_TO_LOCATION
from schemathesis.core.storage import atomic_write_text
from schemathesis.core.transport import HttpMethod
from schemathesis.engine import Status

if TYPE_CHECKING:
    from schemathesis.config import SanitizationConfig
    from schemathesis.engine.recorder import ScenarioRecorder


CRASH_FORMAT_VERSION = 1
MANIFEST_FILENAME = "manifest.json"

_FILENAME_NON_ALNUM = re.compile(r"[^A-Za-z0-9]+")


def _slug(text: str) -> str:
    return _FILENAME_NON_ALNUM.sub("_", text).strip("_") or "operation"


def _failure_fingerprint(failure: Failure) -> str:
    payload = f"{type(failure).__name__}|{failure.operation}|{failure._unique_key}"
    return hashlib.sha256(payload.encode()).hexdigest()[:8]


@dataclass(slots=True)
class CrashManifest:
    format_version: int
    schemathesis_version: str
    schema_location: str
    base_url: str
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "format_version": self.format_version,
            "schemathesis_version": self.schemathesis_version,
            "schema_location": self.schema_location,
            "base_url": self.base_url,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CrashManifest:
        format_version = data["format_version"]
        if not isinstance(format_version, int) or format_version != CRASH_FORMAT_VERSION:
            raise ValueError(f"Unsupported format_version: {format_version!r}")
        return cls(
            format_version=format_version,
            schemathesis_version=data["schemathesis_version"],
            schema_location=data["schema_location"],
            base_url=data["base_url"],
            created_at=data["created_at"],
        )


@dataclass(slots=True)
class CrashCheck:
    name: str
    status: str
    message: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CrashCheck:
        return cls(name=data["name"], status=data["status"], message=data["message"])

    def to_dict(self) -> dict[str, str]:
        return {"name": self.name, "status": self.status, "message": self.message}


@dataclass(slots=True)
class CrashLink:
    operation_id: str
    parameters: dict[str, str]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CrashLink:
        return cls(operation_id=data["operationId"], parameters=data["parameters"])

    def to_dict(self) -> dict[str, Any]:
        return {"operationId": self.operation_id, "parameters": self.parameters}


@dataclass(slots=True)
class CrashStep:
    method: HttpMethod
    url: str
    url_template: str
    request_headers: dict[str, str]
    request_body: str | None
    response_status: int
    response_headers: dict[str, str]
    response_body: str
    link: CrashLink | None
    checks: list[CrashCheck]
    meta: dict[str, Any] | None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CrashStep:
        response = data["response"]
        return cls(
            method=data["method"],
            url=data["url"],
            url_template=data["url_template"],
            request_headers=data["headers"],
            request_body=data["body"],
            response_status=response["status_code"],
            response_headers=response.get("headers", {}),
            response_body=response["body"],
            link=CrashLink.from_dict(data["link"]) if data["link"] is not None else None,
            checks=[CrashCheck.from_dict(c) for c in data["checks"]],
            meta=data.get("meta"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "method": self.method,
            "url": self.url,
            "url_template": self.url_template,
            "headers": self.request_headers,
            "body": self.request_body,
            "response": {
                "status_code": self.response_status,
                "headers": self.response_headers,
                "body": self.response_body,
            },
            "link": self.link.to_dict() if self.link is not None else None,
            "checks": [c.to_dict() for c in self.checks],
            "meta": self.meta,
        }


@dataclass(slots=True)
class CrashFile:
    operation: str
    method: HttpMethod
    path_template: str
    fingerprint: str
    case_id: str
    code_sample: str
    sequence: list[CrashStep]

    def filename(self) -> str:
        terminal = self.sequence[-1]
        check_name = terminal.checks[0].name if terminal.checks else "unknown"
        return f"{_slug(self.operation)}_{check_name}_{self.fingerprint}.json"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CrashFile:
        sequence = [CrashStep.from_dict(step) for step in data["sequence"]]
        assert sequence, "CrashFile must have at least one step"
        return cls(
            operation=data["operation"],
            method=data["method"],
            path_template=data["path_template"],
            fingerprint=data["fingerprint"],
            case_id=data["case_id"],
            code_sample=data["code_sample"],
            sequence=sequence,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "operation": self.operation,
            "method": self.method,
            "path_template": self.path_template,
            "fingerprint": self.fingerprint,
            "case_id": self.case_id,
            "code_sample": self.code_sample,
            "sequence": [step.to_dict() for step in self.sequence],
        }


class CrashWriter:
    __slots__ = ("_directory",)

    def __init__(self, *, directory: Path) -> None:
        self._directory = directory

    def open(self, *, schema_location: str, base_url: str) -> None:
        self._directory.mkdir(parents=True, exist_ok=True)
        manifest = CrashManifest(
            format_version=CRASH_FORMAT_VERSION,
            schemathesis_version=schemathesis.__version__,
            schema_location=schema_location,
            base_url=base_url,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        atomic_write_text(self._directory / MANIFEST_FILENAME, json.dumps(manifest.to_dict(), indent=2))

    def write(self, crash: CrashFile) -> None:
        assert crash.sequence, "CrashFile must have at least one step"
        path = self._directory / crash.filename()
        if path.exists():
            return
        atomic_write_text(path, json.dumps(crash.to_dict(), indent=2))

    def remove_by_operation(self, operation: str) -> None:
        prefix = f"{_slug(operation)}_"
        for path in self._directory.glob(f"{prefix}*.json"):
            if path.name != MANIFEST_FILENAME:
                path.unlink()

    def remove_files(self, filenames: set[str]) -> None:
        for filename in filenames:
            try:
                (self._directory / filename).unlink()
            except FileNotFoundError:
                pass


def load_manifest(directory: Path) -> CrashManifest | None:
    manifest_path = directory / MANIFEST_FILENAME
    try:
        return CrashManifest.from_dict(json.loads(manifest_path.read_text()))
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return None


def build_crashes_from_recorder(
    *,
    recorder: ScenarioRecorder,
    failing_case_id: str,
    sanitization: SanitizationConfig,
) -> list[CrashFile]:
    """Build one `CrashFile` per failing check on the terminal case."""
    chain = []
    node_id: str | None = failing_case_id
    while node_id is not None:
        node = recorder.cases[node_id]
        chain.append(node)
        node_id = node.parent_id
    chain.reverse()

    base_sequence: list[CrashStep] = []
    for index, node in enumerate(chain):
        case = node.value
        case_id = case.id

        interaction = recorder.interactions.get(case_id)
        assert interaction is not None and interaction.response is not None

        request = interaction.request
        raw_headers = dict(request.headers)
        sanitize_value(raw_headers, config=sanitization)
        request_headers = {k: v[0] if isinstance(v, list) else v for k, v in raw_headers.items()}
        request_body = request.encoded_body
        request_url = request.uri

        response = interaction.response
        response_status = response.status_code
        response_headers: dict[str, str] = {k: v[0] if v else "" for k, v in response.headers.items()}
        response_body = response.content.decode(response.encoding or "utf-8", errors="replace")

        link: CrashLink | None = None
        if index > 0 and node.transition is not None:
            transition = node.transition
            link_parameters: dict[str, str] = {}
            for container_name, parameter_dict in transition.parameters.items():
                location = CONTAINER_TO_LOCATION.get(container_name)
                if location is not None:
                    for parameter_name, extracted in parameter_dict.items():
                        link_parameters[f"{location.value}.{parameter_name}"] = extracted.definition
            link = CrashLink(operation_id=transition.id, parameters=link_parameters)

        base_url = case.operation.base_url or ""
        url_template = f"{base_url.rstrip('/')}{case.path}"

        meta_dict = case.meta.to_dict() if case.meta is not None else None

        base_sequence.append(
            CrashStep(
                method=case.method,
                url=request_url,
                url_template=url_template,
                request_headers=request_headers,
                request_body=request_body,
                response_status=response_status,
                response_headers=response_headers,
                response_body=response_body,
                link=link,
                checks=[],
                meta=meta_dict,
            )
        )

    failing_checks = [
        check
        for check in recorder.checks[failing_case_id]
        if check.status == Status.FAILURE and check.failure_info is not None
    ]
    assert failing_checks

    terminal_case = chain[-1].value
    crashes: list[CrashFile] = []
    for check in failing_checks:
        failure_info = check.failure_info
        assert failure_info is not None
        sequence = [
            CrashStep(
                method=step.method,
                url=step.url,
                url_template=step.url_template,
                request_headers=step.request_headers,
                request_body=step.request_body,
                response_status=step.response_status,
                response_headers=step.response_headers,
                response_body=step.response_body,
                link=step.link,
                checks=step.checks,
                meta=step.meta,
            )
            for step in base_sequence
        ]
        sequence[-1].checks = [
            CrashCheck(
                name=check.name,
                status=check.status.value,
                message=str(failure_info.failure),
            )
        ]
        crashes.append(
            CrashFile(
                operation=recorder.label,
                method=terminal_case.method,
                path_template=terminal_case.path,
                fingerprint=_failure_fingerprint(failure_info.failure),
                case_id=terminal_case.id,
                code_sample=failure_info.code_sample,
                sequence=sequence,
            )
        )
    return crashes
