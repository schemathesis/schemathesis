from __future__ import annotations

import os
import platform
import re
import sys
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING

import click
import pytest
import requests
from click.testing import Result
from syrupy.extensions.single_file import SingleFileSnapshotExtension, WriteMode

import schemathesis
from schemathesis.core.version import SCHEMATHESIS_VERSION

if TYPE_CHECKING:
    from _pytest.fixtures import FixtureRequest
    from syrupy.types import PropertyFilter, PropertyMatcher


FLASK_MARKERS = ("* Serving Flask app", "* Debug mode")
PACKAGE_ROOT = Path(schemathesis.__file__).parent
TEST_ROOT = Path(__file__).parent.parent
SITE_PACKAGES = requests.__file__.split("requests")[0]
IS_WINDOWS = platform.system() == "Windows"
EXAMPLE_UUID = "e32ab85ed4634c38a320eb0b22460da9"


TRACEBACK_HEADER = "Traceback (most recent call last):"


@contextmanager
def keep_cwd():
    cwd = os.getcwd()
    try:
        yield
    finally:
        os.chdir(cwd)


def _collapse_tracebacks(data: str) -> str:
    """Replace each rendered stack with a placeholder, keeping the exception line that follows it."""
    lines = data.splitlines()
    collapsed: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        stripped = line.strip()
        if stripped != TRACEBACK_HEADER:
            collapsed.append(line)
            index += 1
            continue
        indent = " " * (len(line) - len(line.lstrip()))
        collapsed.append(f"{indent}<TRACEBACK>")
        index += 1
        # Frames are indented past the header; the exception line that closes the stack is not.
        while index < len(lines) and (not lines[index].strip() or lines[index].startswith(f"{indent} ")):
            index += 1
    return "\n".join(collapsed)


@dataclass
class CliSnapshotConfig:
    request: FixtureRequest
    replace_server_host: bool = True
    replace_tmp_dir: bool = True
    replace_duration: bool = True
    replace_error_codes: bool = True
    replace_test_case_id: bool = True
    replace_uuid: bool = True
    replace_response_time: bool = True
    replace_seed: bool = True
    replace_reproduce_with: bool = False
    replace_test_cases: bool = True
    replace_generated: bool = False
    replace_phase_statistic: bool = False
    replace_cycle_metrics: bool = False
    replace_stateful_statistic: bool = True
    remove_last_line: bool = False
    replace: bool = True
    replace_traceback: bool = False
    replace_invalid_component: bool = False

    @classmethod
    def from_request(cls, request: FixtureRequest) -> CliSnapshotConfig:
        marker = request.node.get_closest_marker("snapshot")
        if marker is not None:
            return cls(request, **marker.kwargs)
        return cls(request)

    @property
    def testdir(self):
        return self.request.getfixturevalue("testdir")

    def serialize(self, data: str) -> str:
        if not self.replace:
            return data
        for step in self._steps():
            data = step(data)
        return data

    def _steps(self) -> list[Callable[[str], str]]:
        # Order matters: later steps match the text earlier steps produce.
        flagged = [
            (self.replace_test_cases, _mask_test_cases),
            (self.replace_generated, _mask_generated),
            (self.replace_server_host, self._mask_server_host),
            (True, _mask_local_ports),
            (self.replace_tmp_dir, self._mask_tmp_dir),
            (True, _mask_config_path),
            (True, _mask_environment),
            (True, _mask_fuzz_counters),
            (self.replace_phase_statistic, _mask_phase_errors),
            (self.replace_cycle_metrics, _mask_cycle_metrics),
            (True, partial(_mask_stateful_counts, links=self.replace_stateful_statistic)),
            (self.replace_traceback, _collapse_tracebacks),
            (True, _drop_traceback_noise),
            (self.replace_error_codes, normalize_connection_errors),
            (self.replace_duration, _mask_durations),
            (self.remove_last_line, _drop_last_line),
            (self.replace_test_case_id, _mask_test_case_ids),
            (self.replace_uuid, _mask_uuids),
            (self.replace_response_time, _mask_slow_response_time),
            (self.replace_seed, _mask_seed),
            (True, _mask_random_values),
            (self.replace_invalid_component, _mask_invalid_component),
            (self.replace_reproduce_with, _mask_reproduce_commands),
            (True, _clean_lines),
        ]
        return [step for enabled, step in flagged if enabled]

    def _mask_server_host(self, data: str) -> str:
        if "server_host" in self.request.fixturenames:
            try:
                data = data.replace(self.request.getfixturevalue("server_host"), "127.0.0.1")
            except LookupError:
                pass
        with keep_cwd():
            return data.replace(Path(self.testdir.tmpdir).as_uri(), "file:///tmp")

    def _mask_tmp_dir(self, data: str) -> str:
        with keep_cwd():
            data = data.replace(str(self.testdir.tmpdir) + os.path.sep, "/tmp/")
            data = data.replace(str(Path(self.testdir.tmpdir).parent) + os.path.sep, "/tmp/")
        # Whatever remains is a sibling per-test directory (e.g. the `tmp_path` fixture). Its numeric
        # suffix depends on allocation order within the session, so strip it instead of recording it.
        data = re.sub(r"/tmp/[A-Za-z0-9_.\-]+\d+" + re.escape(os.path.sep), "/tmp/", data)
        if os.path.sep != "/":
            data = re.sub(r"/tmp/\S+", lambda match: match.group(0).replace(os.path.sep, "/"), data)
        return data


def _mask_test_cases(data: str) -> str:
    # All-skipped and all-errored runs: the skipped/errored split varies across environments.
    data = re.sub(r"Test cases:\n  (\d+) generated, \1 (?:skipped|errored)", "Test cases:\n  N generated", data)
    data = re.sub(r"Test cases:\n  \d+ generated, \d+ errored, \d+ skipped", "Test cases:\n  N generated", data)
    # Cases with failures (skip count optional — non-deterministic, so not snapshot-tested)
    data = re.sub(
        r"Test cases:\n  (\d+) generated, (\d+) found (\d+) unique failures?(?:, \d+ errored)?(?:, \d+ skipped)?",
        "Test cases:\n  N generated, N found N unique failures",
        data,
    )
    # Cases with passed (skip count optional)
    return re.sub(
        r"Test cases:\n  (\d+) generated, (\d+) passed(?:, \d+ errored)?(?:, \d+ skipped)?",
        "Test cases:\n  N generated, N passed",
        data,
    )


def _mask_generated(data: str) -> str:
    return re.sub(r"\d+ generated", "N generated", data)


def _mask_local_ports(data: str) -> str:
    return re.sub(r"(https?)://([^/@\s]+@)?127\.0\.0\.1:[0-9]{3,}", r"\1://\g<2>127.0.0.1", data)


def _mask_config_path(data: str) -> str:
    if "Configuration:" not in data:
        return data
    lines = []
    for line in data.splitlines():
        normalized = click.unstyle(line)
        stripped = normalized.lstrip()
        if stripped.startswith("Configuration:"):
            indent = " " * (len(normalized) - len(stripped))
            lines.append(f"{indent}Configuration:    /tmp/config.toml")
        else:
            lines.append(line)
    return "\n".join(lines)


def _mask_environment(data: str) -> str:
    data = data.replace(str(PACKAGE_ROOT), "/package-root")
    data = data.replace(str(TEST_ROOT), "/test-root")
    data = re.sub(
        "❌  Failed to load configuration file from .*toml$",
        "❌  Failed to load configuration file from config.toml",
        data,
        flags=re.MULTILINE,
    )
    version_line = "Schemathesis dev"
    data = data.replace(f"Schemathesis v{SCHEMATHESIS_VERSION}", version_line)
    data = re.sub("━+", "━" * len(version_line), data)
    data = data.replace(str(SITE_PACKAGES), "/site-packages/")
    data = re.sub(", line [0-9]+,", ", line XXX,", data)
    return re.sub(r"Scenarios:.*\d+", r"Scenarios:    N", data)


def _mask_fuzz_counters(data: str) -> str:
    if "Stop reason:" not in data:
        return data
    # Fuzz-specific output: scenario count and counters vary with time and machine speed
    data = re.sub(r"✅ \d+ scenarios?\b", "✅ N scenarios", data)
    data = re.sub(r"⏭  \d+ without checks", "⏭  N without checks", data)
    data = re.sub(r"❌ \d+ unique failures?\b", "❌ N unique failures", data)
    data = re.sub(r"🚫 \d+ errors?", "🚫 N errors", data)
    return re.sub(r"Tested: \d+", "Tested: N", data)


def _mask_phase_errors(data: str) -> str:
    return re.sub("🚫 [0-9]+ errors", "🚫 1 error", data)


def _mask_cycle_metrics(data: str) -> str:
    data = re.sub(r"\d+ cases", "N cases", data)
    data = re.sub(r"Scenarios:\s+\d+", "Scenarios:    N", data)
    # Whether a scenario passed before the failing one depends on the draw order.
    return re.sub(r"(?m)^(\s*)(?:✅ \d+ passed {2})?❌ \d+ failed[ \t]*$", r"\1❌ N failed", data)


def _mask_stateful_counts(data: str, *, links: bool) -> str:
    if "Stateful" not in data:
        return data
    if links:
        data = re.sub(r"API Links:.*\d+ covered", r"API Links:    N covered", data)
    before, after = data.split("Stateful", 1)
    after = re.sub(r"\d+ passed", "N passed", after)
    return before + "Stateful" + re.sub(r"\d+ without checks", "N without checks", after)


def _drop_traceback_noise(data: str) -> str:
    if TRACEBACK_HEADER not in data:
        return data
    lines = [line for line in data.splitlines() if set(line) not in ({" ", "^"}, {" ", "^", "~"})]
    comprehension_ids = [idx for idx, line in enumerate(lines) if line.strip().endswith("comp>")]
    # Drop frames that are related to comprehensions
    for idx in comprehension_ids[::-1]:
        lines.pop(idx)
        lines.pop(idx)
    if platform.system() == "Windows":
        for idx, line in enumerate(lines):
            if line.strip().startswith("File") and "line" in line:
                lines[idx] = line.replace("\\", "/")
    return "\n".join(lines)


def _mask_durations(data: str) -> str:
    data = re.sub(r"It took [0-9]+\.[0-9]{2}s", "It took 0.50s", data)
    data = re.sub(r"\(in [0-9]+\.[0-9]{2}s\)", "(in 0.00s)", data)
    data = re.sub(r"after [0-9]+\.[0-9]{2}s", "after 0.00s", data).strip()
    lines = data.splitlines()
    for index, line in enumerate(lines):
        if line.startswith("=") and re.search(r"in [0-9]+\.[0-9]{2}s", line):
            lines[index] = re.sub(r"in [0-9]+\.[0-9]{2}s", "in 1.00s", line).strip("=").center(80, "=")
    return "\n".join(lines) + "\n"


def _drop_last_line(data: str) -> str:
    return "\n".join(data.splitlines()[:-1])


def _mask_test_case_ids(data: str) -> str:
    lines = data.splitlines()
    for idx, line in enumerate(lines):
        if re.match(r".*\d+\. Test Case ID", line):
            sequential_id = line.split(".")[0]
            lines[idx] = f"{sequential_id}. Test Case ID: <PLACEHOLDER>"
        elif re.match(r"\s+st replay \S+", line):
            lines[idx] = "    st replay <PLACEHOLDER>"
    return "\n".join(lines) + "\n"


def _mask_uuids(data: str) -> str:
    return re.sub(r"\b[0-9a-fA-F]{32}\b", EXAMPLE_UUID, data)


def _mask_slow_response_time(data: str) -> str:
    return re.sub(
        r"Actual: (\d+\.\d+)ms",
        lambda match: "Actual: 500.00ms" if float(match.group(1)) >= 500 else match.group(0),
        data,
    )


def _mask_seed(data: str) -> str:
    data = re.sub(r"--seed=\d+", "--seed=42", data)
    return re.sub(r"Seed: \d+", "Seed: 42", data)


def _mask_random_values(data: str) -> str:
    # Reauth count varies with how many generated cases hit the stale token.
    data = re.sub(r"Re-authenticated \d+ times?", "Re-authenticated N times", data)
    # The hint lists generated property names verbatim; they are random Hypothesis output.
    return re.sub(
        r"contains \d+ additional (?:property|properties) not defined in the schema \(.*?\)\. The server",
        "contains <N> additional properties not defined in the schema (<NAMES>). The server",
        data,
        flags=re.DOTALL,
    )


def _mask_invalid_component(data: str) -> str:
    # Same seed picks different mutator outputs across CI Pythons (constraint
    # violation vs. syntax fuzzing vs. format violation, etc.). Collapse the
    # whole `Invalid component:` body to a placeholder so version-specific
    # case selection doesn't break the snapshot.
    data = re.sub(
        r"^([ \t]*Invalid component:)[^\n]*(?:\n[ \t]+- [^\n]*)*",
        r"\1 <PLACEHOLDER>",
        data,
        flags=re.MULTILINE,
    )
    # Negative-fuzzing variance can produce body content with non-printable bytes, and the
    # curl advisory for those only appears where shell detection comes up empty — true on
    # CI, false on a developer machine. Strip the warning and the blank line under it, which
    # the reproduce block indents, so spacing matches the warning-free runs.
    return re.sub(
        r"^[ \t]*⚠️[ \t]+Request body contains non-printable characters\.[^\n]*\n[ \t]*\n",
        "",
        data,
        flags=re.MULTILINE,
    )


def _mask_reproduce_commands(data: str) -> str:
    # One placeholder per run of curl lines and the identity labels above them: a stateful reproduction
    # has a varying number of steps. The `st replay <id>` line is masked separately by `replace_test_case_id`.
    lines = []
    seen = False
    for line in data.splitlines():
        if "curl" in line or line.lstrip().startswith("# as "):
            if not seen:
                lines.append("    <PLACEHOLDER>")
                seen = True
        else:
            seen = False
            lines.append(line)
    return "\n".join(lines) + "\n"


TRANSIENT_LINE_PREFIXES = (
    "🕛 ",
    "🕐 ",
    "🕑 ",
    "🕒 ",
    "🕓 ",
    "🕔 ",
    "🕕 ",
    "🕖 ",
    "🕗 ",
    "🕘 ",
    "🕙 ",
    "🕚 ",
    "⠋",
    "⠙",
    "⠹",
    "⠸",
    "⠼",
    "⠴",
    "⠦",
    "⠧",
    "⠇",
    "⠏",
    "0:0",
    # Fuzz live-display lines (transient, not cleared on non-TTY)
    "No issues found yet",
    "Last new failure:",
)


def _is_transient(line: str) -> bool:
    return (
        any(marker in line for marker in FLASK_MARKERS)
        or (line.lstrip().startswith(TRANSIENT_LINE_PREFIXES) and not _is_phase_header(line))
        or re.match(r"    [❌🚫]", line) is not None
    )


def _is_phase_header(line: str) -> bool:
    # A finished phase can carry the same clock icon as the live progress lines.
    return re.search(r" \(in \d+\.\d{2}s\)$", line.rstrip()) is not None


def _normalize_line(line: str) -> str:
    line = click.unstyle(line)
    if line.endswith("Schema Loading Error") and line.strip() != "Schema Loading Error":
        # It is written at the end of the current line and does not properly rewrite the current line
        # on all terminals
        return "Schema Loading Error"
    if IS_WINDOWS and ("Loading specification" in line or "Loaded specification" in line):
        return line.replace("\\", "/")
    if IS_WINDOWS and line.startswith("Replaying ") and " from " in line:
        # `st replay` echoes the crash file/dir path, which uses `\` on Windows.
        return line.replace("\\", "/")
    return line


def _has_unstable_blank_lines(data: str) -> bool:
    return (
        "Stop reason:" in data
        or "Empty test suite" in data
        # Runs that tested nothing end with a banner naming the cause.
        or "The schema defines no API operations" in data
        or "No operations matched the filters" in data
        or "Every selected operation was skipped" in data
        or "No links for stateful testing" in data
        # Same no-tests path, but a warning or an interrupt replaced the "Empty test suite" banner.
        or ("No test cases were generated" in data and ("Warnings:" in data or "Interrupted after" in data))
    )


def _collapse_blank_runs(lines: list[str]) -> list[str]:
    collapsed: list[str] = []
    for line in lines:
        if line == "" and collapsed and collapsed[-1] == "":
            continue
        collapsed.append(line)
    return collapsed


def _clean_lines(data: str) -> str:
    lines = []
    for line in data.splitlines():
        normalized = _normalize_line(line)
        if normalized == "Schema Loading Error" or not _is_transient(normalized):
            lines.append(normalized.rstrip())
    if _has_unstable_blank_lines(data):
        # Rich Live progress widgets leave extra blank lines on non-TTY consoles;
        # collapse runs of consecutive blanks to one. Triggers on st fuzz output
        # (after "Stop reason:") and on the st run path where nothing was generated,
        # where the probing progress doesn't clean up identically across platforms.
        lines = _collapse_blank_runs(lines)
    lines = clean_unit_tests(lines)
    lines = clean_stateful_tests(lines)
    return re.sub(r"\n{4,}", "\n\n\n", "\n".join(lines)).strip() + "\n"


def clean_unit_tests(lines):
    capabilities_idx = None
    for idx, line in enumerate(lines):
        if "API capabilities" in line:
            capabilities_idx = idx
            break
        if "API probing" in line:
            # No capability block follows; probing failed/skipped.
            probing_idx = idx + 2
            break
    else:
        return lines

    if capabilities_idx is not None:
        # Keep every row of the capability block up to the next phase marker rather than
        # trimming to a fixed line count; lets future capability rows render without losing them.
        probing_idx = capabilities_idx + 1
        while probing_idx < len(lines) and not any(
            f"{phase} (in" in lines[probing_idx] for phase in ("Examples", "Coverage", "Fuzzing")
        ):
            probing_idx += 1

    indices = []
    for idx, line in enumerate(lines[probing_idx:], start=probing_idx):
        if any(f"{phase} (in" in line for phase in ("Examples", "Coverage", "Fuzzing")):
            indices.append(idx)

    if not indices:
        return lines

    output = lines[:probing_idx]
    for idx in indices[:-1]:
        output += lines[idx : idx + 4]
    output += lines[indices[-1] :]
    return output


def _find(lines: list[str], offsets: dict[str, int]) -> int | None:
    """Index of the first line containing any marker, shifted by that marker's offset."""
    for idx, line in enumerate(lines):
        for marker, offset in offsets.items():
            if marker in line:
                return idx + offset
    return None


def clean_stateful_tests(lines):
    start_idx = _find(lines, {"Fuzzing (in": 3})
    if start_idx is None:
        # An unreachable server has no capability rows under the header.
        start_idx = _find(lines, {"API probing failed": 1, "API capabilities: unknown": 0, "API capabilities": 3})
    end_idx = _find(lines, {"Stateful (in": 0})
    if start_idx is None or end_idx is None:
        return lines
    # Keep the blank line that separates the Stateful block from the one above it.
    if end_idx > start_idx + 1 and lines[end_idx - 1] == "":
        end_idx -= 1
    return lines[: start_idx + 1] + lines[end_idx:]


def normalize_connection_errors(data: str) -> str:
    data = (
        data.replace("Errno 111", "Error NUM")
        .replace("Errno 61", "Error NUM")
        # Connection reset by peer.
        .replace("Errno 104", "Error NUM")
        .replace("Errno 54", "Error NUM")
        .replace("WinError 10061", "Error NUM")
        .replace("WinError 10054", "Error NUM")
        .replace("Cannot connect to proxy.", "Unable to connect to proxy")
    )
    return data.replace(
        "No connection could be made because the target machine actively refused it", "Connection refused"
    ).replace("An existing connection was forcibly closed by the remote host", "Connection reset by peer")


@pytest.fixture
def snapshot_cli(request, snapshot):
    config = CliSnapshotConfig.from_request(request)
    snapshot_suffix = request.node.get_closest_marker("snapshot_suffix")

    class CliSnapshotExtension(SingleFileSnapshotExtension):
        _write_mode = WriteMode.TEXT

        def serialize(
            self,
            data: Result | pytest.RunResult,
            *,
            exclude: PropertyFilter | None = None,
            include: PropertyFilter | None = None,
            matcher: PropertyMatcher | None = None,
        ) -> str:
            stdout = ""
            if isinstance(data, Result):
                exit_code = data.exit_code
                if data.stdout_bytes:
                    stdout = data.stdout
                if data.stderr_bytes:
                    stdout += data.stderr
            else:
                exit_code = data.ret
                stdout = data.stdout.str() + data.stderr.str()
            serialized = f"Exit code: {exit_code}"
            if stdout:
                serialized += f"\n---\nStdout:\n{stdout}"
            return config.serialize(serialized).replace("\r\n", "\n").replace("\r", "\n")

        @classmethod
        def get_snapshot_name(cls, *, test_location, index=0) -> str:
            base_name = super().get_snapshot_name(test_location=test_location, index=index)
            if snapshot_suffix is not None:
                suffix = str(snapshot_suffix.args[0])
                return f"{base_name}.{suffix}"
            return base_name

    class SnapshotAssertion(snapshot.__class__):
        def rebuild(self):
            return self.use_extension(extension_class=CliSnapshotExtension)

    snapshot.__class__ = SnapshotAssertion
    return snapshot.rebuild()


@pytest.fixture
def snapshot_html(snapshot, monkeypatch):
    # The report shows the command only for a known entrypoint, and xdist workers run with `argv[0] == "-c"`
    monkeypatch.setattr(sys, "argv", ["/venv/bin/pytest"])

    class HtmlSnapshotExtension(SingleFileSnapshotExtension):
        _write_mode = WriteMode.TEXT
        file_extension = "html"

        def serialize(self, data, *, exclude=None, include=None, matcher=None):
            data = re.sub(r"<style>.*?</style>", "<style><CSS></style>", data, count=1, flags=re.DOTALL)
            data = re.sub(r"<script>.*?</script>", "<script><JS></script>", data, flags=re.DOTALL)
            data = data.replace(f"v{SCHEMATHESIS_VERSION}", "v<VERSION>")
            data = normalize_connection_errors(data)
            data = re.sub(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} UTC", "<GENERATED-AT>", data)
            data = re.sub(r"127\.0\.0\.1:\d+", "127.0.0.1:<PORT>", data)
            data = re.sub(r'(Duration</span><span class="m-value">)[^<]+', r"\1<DURATION>", data)
            data = re.sub(r"(Finished in |Stopped by user after )[^<]+", r"\1<DURATION>", data)
            data = re.sub(
                r'(class="case-id permalink-id" href="#case-)\w+(" title="Link to this case">)[^<]+',
                r"\1<CASE-ID>\2<CASE-ID>",
                data,
            )
            data = re.sub(r'id="case-\w+"', 'id="case-<CASE-ID>"', data)
            data = re.sub(r"st replay \w+", "st replay <CASE-ID>", data)
            data = re.sub(r"X-Schemathesis-TestCaseId: \w+", "X-Schemathesis-TestCaseId: <CASE-ID>", data)
            data = re.sub(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", "<UUID>", data)
            data = re.sub(r"File &quot;[^&]+&quot;, line \d+", "File &quot;<PATH>&quot;, line <LINE>", data)
            # Python 3.11+ underlines the failing expression; older versions do not.
            data = re.sub(r"(?m)^[ ~^]*[~^][ ~^]*\n", "", data)
            # In-process runs report the pytest command line.
            data = re.sub(
                r'<span class="tk">Command</span>.*?aria-label="Copy command">Copy</button></span>',
                '<span class="tk">Command</span><COMMAND>',
                data,
            )
            # Whether the timeline is compact, and where its marks fall, depends on run time; its own tests cover it.
            data = re.sub(r'<section class="timeline[^"]*".*?</section>', "<TIMELINE>", data, flags=re.DOTALL)
            data = re.sub(r'href="#case-\w+"', 'href="#case-<CASE-ID>"', data)
            return data.replace("\r\n", "\n").replace("\r", "\n").rstrip("\n") + "\n"

    return snapshot.use_extension(extension_class=HtmlSnapshotExtension)
