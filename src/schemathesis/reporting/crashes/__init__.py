from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

import schemathesis
from schemathesis.core import NOT_SET
from schemathesis.core.failures import Failure, is_reproducible_failure
from schemathesis.core.output.sanitization import sanitize_url, sanitize_value
from schemathesis.core.parameters import CONTAINER_TO_LOCATION
from schemathesis.core.storage import atomic_write_text
from schemathesis.core.transforms import deepclone
from schemathesis.core.transport import HttpMethod
from schemathesis.engine import Status
from schemathesis.reporting.crashes.encoding import decode_case_body, encode_case_body, from_json_safe, to_json_safe

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
    """Run-level metadata for a crash directory: format version, schema location, base URL."""

    format_version: int
    schemathesis_version: str
    schema_location: str
    base_url: str
    created_at: str

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
    """A check that failed on a step, with its message."""

    name: str
    status: str
    message: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CrashCheck:
        return cls(name=data["name"], status=data["status"], message=data["message"])


@dataclass(slots=True)
class CrashLink:
    """A stateful link: how a step's parameters were extracted from the previous response."""

    operation_id: str
    parameters: dict[str, str]
    # The link's `requestBody` definition (dict/list/expression), re-evaluated against the live response on replay.
    request_body: Any = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"operationId": self.operation_id, "parameters": self.parameters}
        if self.request_body is not None:
            result["requestBody"] = self.request_body
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CrashLink:
        return cls(
            operation_id=data["operationId"], parameters=data["parameters"], request_body=data.get("requestBody")
        )


@dataclass(slots=True)
class CrashStep:
    """One request/response in a recorded scenario, with everything needed to replay it."""

    method: HttpMethod
    url: str
    url_template: str
    request_headers: dict[str, str]
    response_status: int
    response_headers: dict[str, str]
    response_body: str
    link: CrashLink | None
    checks: list[CrashCheck]
    meta: dict[str, Any] | None
    path: str = ""
    path_parameters: dict[str, Any] = field(default_factory=dict)
    query: dict[str, Any] = field(default_factory=dict)
    case_headers: dict[str, Any] = field(default_factory=dict)
    cookies: dict[str, Any] = field(default_factory=dict)
    case_body: Any = NOT_SET
    media_type: str | None = None
    # Index of the step this link extracts from — the recorded parent, not always the previous step. None at root.
    parent_index: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "method": self.method,
            "url": self.url,
            "url_template": self.url_template,
            "headers": self.request_headers,
            "response": {
                "status_code": self.response_status,
                "headers": self.response_headers,
                "body": self.response_body,
            },
            "link": self.link.to_dict() if self.link is not None else None,
            "checks": [asdict(c) for c in self.checks],
            "meta": self.meta,
            "path": self.path,
            "path_parameters": to_json_safe(self.path_parameters),
            "query": to_json_safe(self.query),
            "case_headers": to_json_safe(self.case_headers),
            "cookies": to_json_safe(self.cookies),
            "case_body": encode_case_body(self.case_body),
            "media_type": self.media_type,
            "parent_index": self.parent_index,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CrashStep:
        response = data["response"]
        return cls(
            method=data["method"],
            url=data["url"],
            url_template=data["url_template"],
            request_headers=data["headers"],
            response_status=response["status_code"],
            response_headers=response.get("headers", {}),
            response_body=response["body"],
            link=CrashLink.from_dict(data["link"]) if data.get("link") is not None else None,
            checks=[CrashCheck.from_dict(c) for c in data["checks"]],
            meta=data.get("meta"),
            path=data.get("path", ""),
            path_parameters=from_json_safe(data.get("path_parameters", {})),
            query=from_json_safe(data.get("query", {})),
            case_headers=from_json_safe(data.get("case_headers", {})),
            cookies=from_json_safe(data.get("cookies", {})),
            case_body=decode_case_body(data["case_body"]) if "case_body" in data else NOT_SET,
            media_type=data.get("media_type"),
            parent_index=data.get("parent_index"),
        )


@dataclass(slots=True)
class CrashFile:
    """A single failing check and the full request sequence that reproduces it."""

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

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CrashFile:
        sequence = [CrashStep.from_dict(step) for step in data["sequence"]]
        if not sequence:
            raise ValueError("CrashFile must have at least one step")
        return cls(
            operation=data["operation"],
            method=data["method"],
            path_template=data["path_template"],
            fingerprint=data["fingerprint"],
            case_id=data["case_id"],
            code_sample=data["code_sample"],
            sequence=sequence,
        )


class CrashWriter:
    __slots__ = ("_directory", "_written")

    def __init__(self, *, directory: Path) -> None:
        self._directory = directory
        self._written: set[str] = set()

    def open(self, *, schema_location: str, base_url: str) -> None:
        self._directory.mkdir(parents=True, exist_ok=True)
        manifest = CrashManifest(
            format_version=CRASH_FORMAT_VERSION,
            schemathesis_version=schemathesis.__version__,
            schema_location=schema_location,
            base_url=base_url,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        atomic_write_text(self._directory / MANIFEST_FILENAME, json.dumps(asdict(manifest), indent=2))

    def write(self, crash: CrashFile) -> None:
        filename = crash.filename()
        # Write each crash once per run so its stored case_id matches the run's output.
        if filename in self._written:
            return
        atomic_write_text(self._directory / filename, json.dumps(crash.to_dict(), indent=2))
        self._written.add(filename)

    def remove_by_operation(self, operation: str) -> None:
        for path in self._directory.glob("*.json"):
            if path.name == MANIFEST_FILENAME:
                continue
            try:
                data = json.loads(path.read_text())
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(data, dict) and data.get("operation") == operation:
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
    failing_checks = [
        check
        for check in recorder.checks.get(failing_case_id, [])
        if check.status == Status.FAILURE
        and check.failure_info is not None
        and is_reproducible_failure(check.failure_info.failure)
    ]
    if not failing_checks:
        return []

    # Some failures (e.g. use-after-free) need a sibling case the parent chain omits; include those too.
    related_case_ids: list[str] = []
    for check in failing_checks:
        failure_info = check.failure_info
        assert failure_info is not None
        related_case_ids.extend(failure_info.failure.related_case_ids())

    chain = [
        recorder.cases[case.id]
        for case in recorder.iter_chain_cases(case_id=failing_case_id, related_case_ids=tuple(related_case_ids))
    ]
    # Map each case to its sequence position so a step can point at the parent its link extracts from.
    index_by_case_id = {node.value.id: position for position, node in enumerate(chain)}

    # Crash files live in the cache; sanitization follows the `output.sanitization` rules.
    enabled = sanitization.enabled

    def sanitize_mapping(value: dict[str, Any]) -> None:
        if enabled:
            sanitize_value(value, config=sanitization)

    def sanitize_uri(url: str) -> str:
        return sanitize_url(url, config=sanitization) if enabled else url

    base_sequence: list[CrashStep] = []
    for index, node in enumerate(chain):
        case = node.value
        case_id = case.id

        interaction = recorder.interactions.get(case_id)
        if interaction is None or interaction.response is None:
            # A step without a recorded response can't be reproduced; skip the whole scenario.
            return []

        request = interaction.request
        raw_headers = dict(request.headers)
        sanitize_mapping(raw_headers)
        request_headers = {k: v[0] if isinstance(v, list) else v for k, v in raw_headers.items()}
        request_url = sanitize_uri(request.uri)

        response = interaction.response
        response_status = response.status_code
        raw_response_headers = dict(response.headers)
        sanitize_mapping(raw_response_headers)
        response_headers: dict[str, str] = {k: v[0] if v else "" for k, v in raw_response_headers.items()}
        response_body = response.text_lossy()

        link: CrashLink | None = None
        if index > 0 and node.transition is not None:
            transition = node.transition
            link_parameters: dict[str, str] = {}
            for container_name, parameter_dict in transition.parameters.items():
                location = CONTAINER_TO_LOCATION[container_name]
                for parameter_name, extracted in parameter_dict.items():
                    link_parameters[f"{location.value}.{parameter_name}"] = extracted.definition
            request_body = transition.request_body.definition if transition.request_body is not None else None
            link = CrashLink(operation_id=transition.id, parameters=link_parameters, request_body=request_body)

        base_url = case.operation.base_url or ""
        url_template = sanitize_uri(f"{base_url.rstrip('/')}{case.path}")

        meta_dict = case.meta.to_dict() if case.meta is not None else None

        case_path_parameters = dict(case.path_parameters)
        sanitize_mapping(case_path_parameters)
        case_query = dict(case.query)
        sanitize_mapping(case_query)
        case_headers = dict(case.headers)
        sanitize_mapping(case_headers)
        case_cookies = dict(case.cookies)
        sanitize_mapping(case_cookies)
        case_body = case.body
        # A JSON body can be rooted at a dict or a list; both may carry sensitive values.
        if isinstance(case_body, (dict, list)):
            case_body = deepclone(case_body)
            if enabled:
                sanitize_value(case_body, config=sanitization)

        base_sequence.append(
            CrashStep(
                method=case.method,
                url=request_url,
                url_template=url_template,
                request_headers=request_headers,
                response_status=response_status,
                response_headers=response_headers,
                response_body=response_body,
                link=link,
                checks=[],
                meta=meta_dict,
                path=case.path,
                path_parameters=case_path_parameters,
                query=case_query,
                case_headers=case_headers,
                cookies=case_cookies,
                case_body=case_body,
                media_type=case.media_type,
                parent_index=index_by_case_id.get(node.parent_id) if node.parent_id is not None else None,
            )
        )

    terminal_case = chain[-1].value
    crashes: list[CrashFile] = []
    for check in failing_checks:
        failure_info = check.failure_info
        assert failure_info is not None
        sequence = [replace(step) for step in base_sequence]
        sequence[-1].checks = [
            CrashCheck(
                name=check.name,
                status=check.status.value,
                message=str(failure_info.failure),
            )
        ]
        crashes.append(
            CrashFile(
                operation=terminal_case.operation.label,
                method=terminal_case.method,
                path_template=terminal_case.path,
                fingerprint=_failure_fingerprint(failure_info.failure),
                case_id=terminal_case.id,
                code_sample=failure_info.code_sample,
                sequence=sequence,
            )
        )
    return crashes
