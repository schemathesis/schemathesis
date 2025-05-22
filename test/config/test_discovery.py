from schemathesis.config import SchemathesisConfig

TOML_CONTENT = """
color = true
suppress-health-check = ["too_slow"]
max-failures = 3
reports = { directory = "reports" }
"""


def test_discover_in_current_directory(tmp_path, monkeypatch):
    # Write the config file in the current directory
    config_file = tmp_path / "schemathesis.toml"
    config_file.write_text(TOML_CONTENT)

    monkeypatch.chdir(tmp_path)

    config = SchemathesisConfig.discover()
    assert config.color is True
    assert config.suppress_health_check == ["too_slow"]
    assert config.max_failures == 3
    assert str(config.reports.directory) == "reports"


def test_discover_in_parent_directory(tmp_path, monkeypatch):
    # Create a config file in the parent directory
    config_file = tmp_path / "schemathesis.toml"
    config_file.write_text(TOML_CONTENT)
    child_dir = tmp_path / "child"
    child_dir.mkdir()

    monkeypatch.chdir(child_dir)

    config = SchemathesisConfig.discover()
    assert config.color is True
    assert config.max_failures == 3


def test_discover_not_found(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    config = SchemathesisConfig.discover()
    assert config.color is None
    assert config.suppress_health_check == []
    assert config.max_failures is None


def test_discover_stops_at_git_root(tmp_path, monkeypatch):
    # Create a structure where a .git folder exists in the parent directory.
    parent = tmp_path / "parent"
    parent.mkdir()
    (parent / ".git").mkdir()  # simulate git repository root
    child = parent / "child"
    child.mkdir()

    # Place a config file outside of the git repo (should not be discovered)
    outside = tmp_path / "outside"
    outside.mkdir()
    config_file = outside / "schemathesis.toml"
    config_file.write_text(TOML_CONTENT)

    monkeypatch.chdir(child)

    config = SchemathesisConfig.discover()
    # Since we stop at the git repo root, no config file should be found.
    assert config.color is None
