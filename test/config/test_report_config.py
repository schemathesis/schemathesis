from pathlib import Path

from schemathesis.config import SchemathesisConfig
from schemathesis.config._report import ReportFormat, ReportsConfig


def test_get_path_directory_formats_have_no_suffix_file_formats_do():
    config = ReportsConfig()
    assert config.get_path(ReportFormat.HTML).suffix == ""
    assert config.get_path(ReportFormat.JUNIT).suffix == ".xml"


def test_reports_config_html_from_dict():
    config = ReportsConfig.from_dict({"html": {"enabled": True}})
    assert config.html.enabled is True
    assert config.get_path(ReportFormat.HTML).name.startswith("html-")
    assert config.get_path(ReportFormat.HTML).suffix == ""


def test_reports_config_html_is_allowed_by_configuration_schema():
    config = SchemathesisConfig.from_dict({"reports": {"html": {"enabled": True}}})
    assert config.projects.default.reports.html.enabled is True


def test_reports_config_html_explicit_path():
    config = ReportsConfig.from_dict({"html": {"path": "my-report"}})
    assert config.html.enabled is True
    assert config.get_path(ReportFormat.HTML) == Path("my-report")


def test_reports_config_update_html():
    config = ReportsConfig()
    config.update(formats=[ReportFormat.HTML])
    assert config.html.enabled is True
    config.update(html_path="custom")
    assert config.get_path(ReportFormat.HTML) == Path("custom")


def test_reports_config_html_stable_path_with_suffix():
    config = ReportsConfig.from_dict({"html": {"enabled": True}})
    assert config.get_stable_path(ReportFormat.HTML, suffix="abc123").name == "html-abc123"
