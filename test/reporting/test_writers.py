from __future__ import annotations

import tempfile
from io import StringIO
from pathlib import Path
from xml.etree import ElementTree

import pytest

from schemathesis.config import ProjectConfig
from schemathesis.config._output import OutputConfig
from schemathesis.config._report import ReportGroupBy
from schemathesis.reporting import HarWriter, JunitXmlWriter, NdjsonWriter, VcrWriter


def test_ndjson_writer_context_manager():
    stream = StringIO()
    with NdjsonWriter(output=stream) as writer:
        writer.open(seed=42, command="st run http://localhost/openapi.json")
    content = stream.getvalue()
    assert '"Initialize"' in content
    assert '"seed":42' in content


def test_ndjson_writer_context_manager_closes_file():
    with tempfile.NamedTemporaryFile(suffix=".ndjson", delete=False) as f:
        path = Path(f.name)
    try:
        with NdjsonWriter(output=path) as writer:
            writer.open(seed=1, command="st run http://localhost/openapi.json")
        assert path.stat().st_size > 0
    finally:
        path.unlink(missing_ok=True)


def test_junitxml_writer_context_manager():
    stream = StringIO()
    with JunitXmlWriter(output=stream) as writer:
        writer.record_error("test_label", "something went wrong")
    content = stream.getvalue()
    assert "schemathesis" in content
    assert "test_label" in content


@pytest.mark.parametrize(
    "writer_cls, kwargs",
    [
        (VcrWriter, {"config": ProjectConfig.from_dict({})}),
        (HarWriter, {"config": ProjectConfig.from_dict({})}),
    ],
    ids=["vcr", "har"],
)
def test_writer_context_manager_no_error_without_open(writer_cls, kwargs):
    with writer_cls(output=StringIO(), **kwargs):
        pass


# --- JUnit XML group-by tests ---


def _parse_junit(stream: StringIO) -> ElementTree.Element:
    """Rewind and parse a StringIO containing JUnit XML."""
    stream.seek(0)
    return ElementTree.parse(stream).getroot()


class TestJunitXmlWriterOperationMode:
    """Tests for the default group_by='operation' mode."""

    def test_default_mode_single_suite(self):
        """Default mode produces a single testsuite named 'schemathesis'."""
        stream = StringIO()
        config = OutputConfig()
        with JunitXmlWriter(output=stream, group_by=ReportGroupBy.OPERATION) as writer:
            writer.record_scenario(label="GET /users", elapsed_sec=1.0, failures=[], skip_reason=None, config=config)
        root = _parse_junit(stream)
        assert root.tag == "testsuites"
        suites = list(root)
        assert len(suites) == 1
        assert suites[0].attrib["name"] == "schemathesis"

    def test_skip_then_success_clears_skip(self):
        """When examples phase skips and coverage phase succeeds, skip is cleared."""
        stream = StringIO()
        config = OutputConfig()
        with JunitXmlWriter(output=stream, group_by=ReportGroupBy.OPERATION) as writer:
            # Examples phase: skip
            writer.record_scenario(
                label="GET /users",
                elapsed_sec=0.0,
                failures=[],
                skip_reason="No examples in schema",
                config=config,
                phase="Examples",
            )
            # Coverage phase: success
            writer.record_scenario(
                label="GET /users", elapsed_sec=0.5, failures=[], skip_reason=None, config=config, phase="Coverage"
            )
        root = _parse_junit(stream)
        testcase = root[0].find("testcase[@name='GET /users']")
        assert testcase is not None
        skipped = testcase.findall("skipped")
        assert len(skipped) == 0, "Skip should be cleared when a later phase succeeds"

    def test_success_then_skip_ignores_skip(self):
        """If a success is recorded before a skip, the skip is ignored."""
        stream = StringIO()
        config = OutputConfig()
        with JunitXmlWriter(output=stream, group_by=ReportGroupBy.OPERATION) as writer:
            # Coverage phase: success (runs first in this scenario)
            writer.record_scenario(
                label="GET /users", elapsed_sec=0.5, failures=[], skip_reason=None, config=config, phase="Coverage"
            )
            # Examples phase: skip
            writer.record_scenario(
                label="GET /users",
                elapsed_sec=0.0,
                failures=[],
                skip_reason="No examples in schema",
                config=config,
                phase="Examples",
            )
        root = _parse_junit(stream)
        testcase = root[0].find("testcase[@name='GET /users']")
        assert testcase is not None
        skipped = testcase.findall("skipped")
        assert len(skipped) == 0, "Skip should be ignored when a prior phase already succeeded"

    def test_skip_only_remains_skipped(self):
        """When only examples phase runs and it skips, the skip is preserved."""
        stream = StringIO()
        config = OutputConfig()
        with JunitXmlWriter(output=stream, group_by=ReportGroupBy.OPERATION) as writer:
            writer.record_scenario(
                label="GET /users",
                elapsed_sec=0.0,
                failures=[],
                skip_reason="No examples in schema",
                config=config,
                phase="Examples",
            )
        root = _parse_junit(stream)
        testcase = root[0].find("testcase[@name='GET /users']")
        assert testcase is not None
        skipped = testcase.findall("skipped")
        assert len(skipped) == 1
        assert skipped[0].text == "No examples in schema"

    def test_error_clears_skip(self):
        """When an error is recorded for a label, any prior skip is cleared."""
        stream = StringIO()
        config = OutputConfig()
        with JunitXmlWriter(output=stream, group_by=ReportGroupBy.OPERATION) as writer:
            writer.record_scenario(
                label="GET /users",
                elapsed_sec=0.0,
                failures=[],
                skip_reason="No examples in schema",
                config=config,
                phase="Examples",
            )
            writer.record_error(label="GET /users", message="Timeout", phase="Coverage")
        root = _parse_junit(stream)
        testcase = root[0].find("testcase[@name='GET /users']")
        assert testcase is not None
        skipped = testcase.findall("skipped")
        assert len(skipped) == 0
        errors = testcase.findall("error")
        assert len(errors) == 1

    def test_backward_compatible_no_phase(self):
        """Writer works without phase parameter (backward compatibility)."""
        stream = StringIO()
        config = OutputConfig()
        with JunitXmlWriter(output=stream) as writer:
            writer.record_scenario(label="GET /users", elapsed_sec=1.0, failures=[], skip_reason=None, config=config)
            writer.record_error(label="POST /items", message="error")
        root = _parse_junit(stream)
        suites = list(root)
        assert len(suites) == 1
        assert suites[0].attrib["name"] == "schemathesis"
        testcases = list(suites[0])
        assert len(testcases) == 2


class TestJunitXmlWriterPhaseMode:
    """Tests for group_by='phase' mode."""

    def test_separate_suites_per_phase(self):
        """Phase mode creates one testsuite per phase."""
        stream = StringIO()
        config = OutputConfig()
        with JunitXmlWriter(output=stream, group_by=ReportGroupBy.PHASE) as writer:
            writer.record_scenario(
                label="GET /users",
                elapsed_sec=0.0,
                failures=[],
                skip_reason="No examples in schema",
                config=config,
                phase="Examples",
            )
            writer.record_scenario(
                label="GET /users", elapsed_sec=0.5, failures=[], skip_reason=None, config=config, phase="Coverage"
            )
            writer.record_scenario(
                label="GET /users", elapsed_sec=1.0, failures=[], skip_reason=None, config=config, phase="Fuzzing"
            )
        root = _parse_junit(stream)
        suites = list(root)
        suite_names = {s.attrib["name"] for s in suites}
        assert suite_names == {
            "schemathesis - Examples",
            "schemathesis - Coverage",
            "schemathesis - Fuzzing",
        }

    def test_phase_skip_isolated(self):
        """In phase mode, skip in examples does not affect coverage suite."""
        stream = StringIO()
        config = OutputConfig()
        with JunitXmlWriter(output=stream, group_by=ReportGroupBy.PHASE) as writer:
            writer.record_scenario(
                label="GET /users",
                elapsed_sec=0.0,
                failures=[],
                skip_reason="No examples in schema",
                config=config,
                phase="Examples",
            )
            writer.record_scenario(
                label="GET /users", elapsed_sec=0.5, failures=[], skip_reason=None, config=config, phase="Coverage"
            )
        root = _parse_junit(stream)
        for suite in root:
            testcase = suite.find("testcase[@name='GET /users']")
            assert testcase is not None
            if suite.attrib["name"] == "schemathesis - Examples":
                assert len(testcase.findall("skipped")) == 1
            elif suite.attrib["name"] == "schemathesis - Coverage":
                assert len(testcase.findall("skipped")) == 0

    def test_phase_mode_multiple_operations(self):
        """Phase mode handles multiple operations in the same phase."""
        stream = StringIO()
        config = OutputConfig()
        with JunitXmlWriter(output=stream, group_by=ReportGroupBy.PHASE) as writer:
            writer.record_scenario(
                label="GET /users", elapsed_sec=0.5, failures=[], skip_reason=None, config=config, phase="Coverage"
            )
            writer.record_scenario(
                label="POST /users", elapsed_sec=0.3, failures=[], skip_reason=None, config=config, phase="Coverage"
            )
        root = _parse_junit(stream)
        suites = list(root)
        assert len(suites) == 1
        assert suites[0].attrib["name"] == "schemathesis - Coverage"
        testcases = list(suites[0])
        assert len(testcases) == 2
        names = {tc.attrib["name"] for tc in testcases}
        assert names == {"GET /users", "POST /users"}

    def test_phase_error_in_correct_suite(self):
        """Errors are placed in the correct phase suite."""
        stream = StringIO()
        with JunitXmlWriter(output=stream, group_by=ReportGroupBy.PHASE) as writer:
            writer.record_error(label="GET /users", message="Timeout", phase="Coverage")
        root = _parse_junit(stream)
        suites = list(root)
        assert len(suites) == 1
        assert suites[0].attrib["name"] == "schemathesis - Coverage"
        testcase = suites[0].find("testcase[@name='GET /users']")
        assert testcase is not None
        assert len(testcase.findall("error")) == 1

    def test_phase_mode_no_phase_falls_back(self):
        """When no phase is provided in phase mode, uses 'other' suite."""
        stream = StringIO()
        config = OutputConfig()
        with JunitXmlWriter(output=stream, group_by=ReportGroupBy.PHASE) as writer:
            writer.record_scenario(label="GET /users", elapsed_sec=0.5, failures=[], skip_reason=None, config=config)
        root = _parse_junit(stream)
        suites = list(root)
        assert len(suites) == 1
        assert suites[0].attrib["name"] == "schemathesis - other"


class TestReportGroupByConfig:
    """Tests for report group-by configuration parsing."""

    def test_default_group_by(self):
        from schemathesis.config._report import ReportsConfig

        config = ReportsConfig.from_dict({})
        assert config.group_by == ReportGroupBy.OPERATION

    def test_group_by_phase(self):
        from schemathesis.config._report import ReportsConfig

        config = ReportsConfig.from_dict({"group-by": "phase"})
        assert config.group_by == ReportGroupBy.PHASE

    def test_group_by_operation_explicit(self):
        from schemathesis.config._report import ReportsConfig

        config = ReportsConfig.from_dict({"group-by": "operation"})
        assert config.group_by == ReportGroupBy.OPERATION

    def test_group_by_with_junit_enabled(self):
        from schemathesis.config._report import ReportsConfig

        config = ReportsConfig.from_dict({"group-by": "phase", "junit": {"enabled": True}})
        assert config.group_by == ReportGroupBy.PHASE
        assert config.junit.enabled is True

    def test_invalid_group_by(self):
        from schemathesis.config._report import ReportsConfig

        with pytest.raises(ValueError):
            ReportsConfig.from_dict({"group-by": "invalid"})
