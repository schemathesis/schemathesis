from __future__ import annotations

import datetime
import json
from io import StringIO
from pathlib import Path
from types import TracebackType
from typing import IO

from schemathesis.config import ProjectConfig
from schemathesis.core.output.sanitization import sanitize_url
from schemathesis.core.transport import Response
from schemathesis.core.version import SCHEMATHESIS_VERSION
from schemathesis.engine import Status
from schemathesis.engine.recorder import CheckNode, Request, ScenarioRecorder
from schemathesis.generation.meta import CoveragePhaseData

TextOutput = IO[str] | StringIO | Path


class VcrWriter:
    """Write network interactions to a VCR YAML cassette file."""

    def __init__(self, output: TextOutput, config: ProjectConfig) -> None:
        self._output = output
        self._config = config
        self._stream: IO[str] | None = None
        self._owned_file: IO[str] | None = None

    def open(self, seed: int | None = None, *, command: str) -> None:
        """Open the output file and write the VCR header."""
        if isinstance(self._output, Path):
            self._owned_file = open(self._output, "w", encoding="utf-8")
            self._stream = self._owned_file
        else:
            self._stream = self._output
        self._stream.write(
            f"command: '{command}'\n"
            f"recorded_with: 'Schemathesis {SCHEMATHESIS_VERSION}'\n"
            f"seed: {seed}\n"
            f"http_interactions:"
        )

    def write(self, recorder: ScenarioRecorder) -> None:
        """Write all interactions from a ScenarioRecorder to the cassette."""
        stream = self._stream
        assert stream is not None
        config = self._config

        def write_header_values(values: list[str]) -> None:
            stream.writelines(f"      - {json.dumps(v)}\n" for v in values)

        if config.output.sanitization.enabled:
            sanitization_keys = config.output.sanitization.keys_to_sanitize
            sensitive_markers = config.output.sanitization.sensitive_markers
            replacement = config.output.sanitization.replacement

            def write_headers(headers: dict[str, list[str]]) -> None:
                for name, values in headers.items():
                    lower_name = name.lower()
                    stream.write(f'      "{name}":\n')
                    if lower_name in sanitization_keys or any(marker in lower_name for marker in sensitive_markers):
                        stream.write(f"      - {json.dumps(replacement)}\n")
                    else:
                        write_header_values(values)
        else:

            def write_headers(headers: dict[str, list[str]]) -> None:
                for name, values in headers.items():
                    stream.write(f'      "{name}":\n')
                    write_header_values(values)

        def write_checks(checks: list[CheckNode]) -> None:
            if not checks:
                stream.write("\n  checks: []")
                return

            stream.write("\n  checks:\n")
            for check in checks:
                message = check.failure_info.failure.title if check.failure_info else None
                message_str = "~" if message is None else repr(message)
                stream.write(
                    f"    - name: '{check.name}'\n"
                    f"      status: '{check.status.name.upper()}'\n"
                    f"      message: {message_str}\n"
                )

        if config.reports.preserve_bytes:

            def write_request_body(request: Request) -> None:
                if request.encoded_body is not None:
                    stream.write(f"\n    body:\n      encoding: 'utf-8'\n      base64_string: '{request.encoded_body}'")

            def write_response_body(response: Response) -> None:
                if response.encoded_body is not None:
                    stream.write(
                        f"    body:\n      encoding: '{response.encoding}'\n      base64_string: '{response.encoded_body}'"
                    )
        else:

            def write_request_body(request: Request) -> None:
                if request.body is not None:
                    string = request.body.decode("utf8", "replace")
                    stream.write("\n    body:\n      encoding: 'utf-8'\n      string: ")
                    write_double_quoted(stream, string)

            def write_response_body(response: Response) -> None:
                if response.content is not None:
                    encoding = response.encoding or "utf8"
                    string = response.content.decode(encoding, "replace")
                    stream.write(f"    body:\n      encoding: '{encoding}'\n      string: ")
                    write_double_quoted(stream, string)

        for case_id, interaction in recorder.interactions.items():
            case = recorder.cases[case_id]

            # Determine status and checks
            if interaction.response is not None:
                if case_id in recorder.checks:
                    checks = recorder.checks[case_id]
                    status = Status.SUCCESS
                    for check in checks:
                        if check.status == Status.FAILURE:
                            status = check.status
                            break
                else:
                    checks = []
                    status = Status.SKIP
            else:
                checks = []
                status = Status.ERROR

            # Write interaction header
            stream.write(f"\n- id: '{case_id}'\n  status: '{status.name}'")

            # Write metadata if present
            meta = case.value.meta
            if meta is not None:
                stream.write(
                    f"\n  generation:\n"
                    f"    time: {meta.generation.time}\n"
                    f"    mode: {meta.generation.mode.value}\n"
                    f"  components:"
                )

                for kind, info in meta.components.items():
                    stream.write(f"\n    {kind.value}:\n      mode: '{info.mode.value}'")

                stream.write(f"\n  phase:\n    name: '{meta.phase.name.value}'\n    data: ")

                if isinstance(meta.phase.data, CoveragePhaseData):
                    stream.write("\n      description: ")
                    write_double_quoted(stream, meta.phase.data.description)
                    stream.write("\n      location: ")
                    write_double_quoted(stream, meta.phase.data.location)
                    stream.write("\n      parameter: ")
                    if meta.phase.data.parameter is not None:
                        write_double_quoted(stream, meta.phase.data.parameter)
                    else:
                        stream.write("null")
                    stream.write("\n      parameter_location: ")
                    if meta.phase.data.parameter_location is not None:
                        write_double_quoted(stream, meta.phase.data.parameter_location)
                    else:
                        stream.write("null")
                else:
                    stream.write("{}")
            else:
                stream.write("\n  metadata: null")

            # Sanitize URL if needed
            if config.output.sanitization.enabled:
                uri = sanitize_url(interaction.request.uri, config=config.output.sanitization)
            else:
                uri = interaction.request.uri

            recorded_at = datetime.datetime.fromtimestamp(interaction.timestamp, datetime.timezone.utc).isoformat()

            stream.write(f"\n  recorded_at: '{recorded_at}'")
            write_checks(checks)
            stream.write(f"\n  request:\n    uri: '{uri}'\n    method: '{interaction.request.method}'\n    headers:\n")
            write_headers(interaction.request.headers)
            write_request_body(interaction.request)

            # Write response
            if interaction.response is not None:
                stream.write(
                    f"\n  response:\n"
                    f"    status:\n"
                    f"      code: '{interaction.response.status_code}'\n"
                    f"      message: {json.dumps(interaction.response.message)}\n"
                    f"    elapsed: '{interaction.response.elapsed}'\n"
                    f"    headers:\n"
                )
                write_headers(interaction.response.headers)
                stream.write("\n")
                write_response_body(interaction.response)
                stream.write(f"\n    http_version: '{interaction.response.http_version}'")
            else:
                stream.write("\n  response: null")

    def close(self) -> None:
        """Close the output file."""
        if self._owned_file is not None:
            self._owned_file.close()
            self._owned_file = None
        self._stream = None

    def __enter__(self) -> VcrWriter:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        self.close()


def write_double_quoted(stream: IO, text: str | None) -> None:
    """Writes a valid YAML string enclosed in double quotes."""
    from yaml.emitter import Emitter

    if text is None:
        stream.write("null")
        return

    # Adapted from `yaml.Emitter.write_double_quoted`:
    #   - Doesn't split the string, therefore doesn't track the current column
    #   - Doesn't encode the input
    #   - Allows Unicode unconditionally
    stream.write('"')
    start = end = 0
    length = len(text)
    while end <= length:
        ch = None
        if end < length:
            ch = text[end]
        if (
            ch is None
            or ch in '"\\\x85\u2028\u2029\ufeff'
            or not ("\x20" <= ch <= "\x7e" or ("\xa0" <= ch <= "\ud7ff" or "\ue000" <= ch <= "\ufffd"))
        ):
            if start < end:
                stream.write(text[start:end])
                start = end
            if ch is not None:
                # Escape character
                if ch in Emitter.ESCAPE_REPLACEMENTS:
                    data = "\\" + Emitter.ESCAPE_REPLACEMENTS[ch]
                elif ch <= "\xff":
                    data = f"\\x{ord(ch):02X}"
                elif ch <= "\uffff":
                    data = f"\\u{ord(ch):04X}"
                else:
                    data = f"\\U{ord(ch):08X}"
                stream.write(data)
                start = end + 1
        end += 1
    stream.write('"')
