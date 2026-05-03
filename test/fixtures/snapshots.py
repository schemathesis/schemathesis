from __future__ import annotations

import os
import platform
import re
from contextlib import contextmanager
from dataclasses import dataclass
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


@contextmanager
def keep_cwd():
    cwd = os.getcwd()
    try:
        yield
    finally:
        os.chdir(cwd)


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
    replace_phase_statistic: bool = False
    replace_stateful_statistic: bool = True
    remove_last_line: bool = False
    replace: bool = True

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
        if self.replace_test_cases:
            # All-skipped case
            data = re.sub(r"Test cases:\n  (\d+) generated, \1 skipped", "Test cases:\n  N generated", data)
            # Cases with failures (skip count optional — non-deterministic, so not snapshot-tested)
            data = re.sub(
                r"Test cases:\n  (\d+) generated, (\d+) found (\d+) unique failures(?:, \d+ skipped)?",
                "Test cases:\n  N generated, N found N unique failures",
                data,
            )
            # Cases with passed (skip count optional)
            data = re.sub(
                r"Test cases:\n  (\d+) generated, (\d+) passed(?:, \d+ skipped)?",
                "Test cases:\n  N generated, N passed",
                data,
            )
        if self.replace_server_host:
            used_fixtures = self.request.fixturenames
            if "server_host" in used_fixtures:
                try:
                    host = self.request.getfixturevalue("server_host")
                    data = data.replace(host, "127.0.0.1")
                except LookupError:
                    pass
            with keep_cwd():
                data = data.replace(Path(self.testdir.tmpdir).as_uri(), "file:///tmp")
        data = re.sub(r"(https?)://127\.0\.0\.1:[0-9]{3,}", r"\1://127.0.0.1", data)
        if self.replace_tmp_dir:
            with keep_cwd():
                data = data.replace(str(self.testdir.tmpdir) + os.path.sep, "/tmp/")
                data = data.replace(str(Path(self.testdir.tmpdir).parent) + os.path.sep, "/tmp/")
        if "Configuration:" in data:
            lines = []
            for line in data.splitlines():
                normalized = click.unstyle(line)
                stripped = normalized.lstrip()
                if stripped.startswith("Configuration:"):
                    indent = " " * (len(normalized) - len(stripped))
                    lines.append(f"{indent}Configuration:    /tmp/config.toml")
                else:
                    lines.append(line)
            data = "\n".join(lines)
        package_root = "/package-root"
        site_packages = "/site-packages/"
        data = data.replace(str(PACKAGE_ROOT), package_root)
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
        data = data.replace(str(SITE_PACKAGES), site_packages)
        data = re.sub(", line [0-9]+,", ", line XXX,", data)
        data = re.sub(r"Scenarios:.*\d+", r"Scenarios:    N", data)
        if "Stop reason:" in data:
            # Fuzz-specific output: scenario count and counters vary with time and machine speed
            data = re.sub(r"✅ \d+ scenarios", "✅ N scenarios", data)
            data = re.sub(r"❌ \d+ unique failures", "❌ N unique failures", data)
            data = re.sub(r"🚫 \d+ errors?", "🚫 N errors", data)
            data = re.sub(r"Tested: \d+", "Tested: N", data)
        if self.replace_phase_statistic:
            data = re.sub("🚫 [0-9]+ errors", "🚫 1 error", data)
        if "Stateful" in data:
            if self.replace_stateful_statistic:
                data = re.sub(r"API Links:.*\d+ covered", r"API Links:    N covered", data)
            before, after = data.split("Stateful", 1)
            after = re.sub(r"\d+ passed", "N passed", after)
            data = before + "Stateful" + after

        if "Traceback (most recent call last):" in data:
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
            data = "\n".join(lines)
        if self.replace_error_codes:
            data = (
                data.replace("Errno 111", "Error NUM")
                .replace("Errno 61", "Error NUM")
                .replace("WinError 10061", "Error NUM")
                .replace("Cannot connect to proxy.", "Unable to connect to proxy")
            )
            data = data.replace(
                "No connection could be made because the target machine actively refused it", "Connection refused"
            )
        if self.replace_duration:
            data = re.sub(r"It took [0-9]+\.[0-9]{2}s", "It took 0.50s", data)
            data = re.sub(r"\(in [0-9]+\.[0-9]{2}s\)", "(in 0.00s)", data)
            data = re.sub(r"after [0-9]+\.[0-9]{2}s", "after 0.00s", data).strip()
            lines = data.splitlines()
            lines[-1] = re.sub(r"in [0-9]+\.[0-9]{2}s", "in 1.00s", lines[-1])
            if "in 1.00s" in lines[-1]:
                lines[-1] = lines[-1].strip("=").center(80, "=")
            data = "\n".join(lines) + "\n"
        if self.remove_last_line:
            lines = data.splitlines()
            data = "\n".join(lines[:-1])
        if self.replace_test_case_id:
            lines = data.splitlines()
            for idx, line in enumerate(lines):
                if re.match(r".*\d+\. Test Case ID", line):
                    sequential_id = line.split(".")[0]
                    lines[idx] = f"{sequential_id}. Test Case ID: <PLACEHOLDER>"
            data = "\n".join(lines) + "\n"
        if self.replace_uuid:
            data = re.sub(r"\b[0-9a-fA-F]{32}\b", EXAMPLE_UUID, data)
        if self.replace_response_time:
            data = re.sub(r"Actual: \d+\.\d+ms", "Actual: 105.00ms", data)
        if self.replace_seed:
            data = re.sub(r"--seed=\d+", "--seed=42", data)
            data = re.sub(r"Seed: \d+", "Seed: 42", data)
        if self.replace_reproduce_with:
            lines = []
            seen = False
            for line in data.splitlines():
                if "curl" in line:
                    if not seen:
                        lines.append("    <PLACEHOLDER>")
                        seen = True
                else:
                    seen = False
                    lines.append(line)
            data = "\n".join(lines) + "\n"
        lines = []
        for line in data.splitlines():
            line = click.unstyle(line)
            if line.endswith("Schema Loading Error"):
                # It is written at the end of the current line and does not properly rewrite the current line
                # on all terminals
                lines.append("Schema Loading Error")
                continue
            if IS_WINDOWS and ("Loading specification" in line or "Loaded specification" in line):
                line = line.replace("\\", "/")
            if (
                any(marker in line for marker in FLASK_MARKERS)
                or line.lstrip().startswith(
                    (
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
                )
                or re.match(r"    [❌🚫]", line)
            ):
                continue
            lines.append(line.rstrip())
        if "Stop reason:" in data or "Empty test suite" in data:
            # Rich Live progress widgets leave extra blank lines on non-TTY consoles;
            # collapse runs of consecutive blanks to one. Triggers on st fuzz output
            # (after "Stop reason:") and on the st run "no tests ran" path
            # ("Empty test suite") where the probing progress doesn't clean up
            # identically across platforms.
            collapsed = []
            for line in lines:
                if line == "" and collapsed and collapsed[-1] == "":
                    continue
                collapsed.append(line)
            lines = collapsed
        lines = clean_unit_tests(lines)
        lines = clean_stateful_tests(lines)
        return "\n".join(lines).strip() + "\n"


def clean_unit_tests(lines):
    for idx, line in enumerate(lines):
        if "API capabilities" in line:
            probing_idx = idx + 4
            break
        if "API probing" in line:
            probing_idx = idx + 2
            break
    else:
        return lines

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


def clean_stateful_tests(lines):
    start_idx = None
    for i, line in enumerate(lines):
        if "Fuzzing (in" in line:
            start_idx = i + 3
            break
    if start_idx is None:
        for i, line in enumerate(lines):
            if "API probing failed" in line:
                start_idx = i + 1
                break
            if "API capabilities" in line:
                start_idx = i + 3
                break

    end_idx = None
    for i, line in enumerate(lines):
        if "Stateful (in" in line:
            end_idx = i
            break

    if start_idx is not None and end_idx is not None:
        return lines[: start_idx + 1] + lines[end_idx:]
    return lines


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
