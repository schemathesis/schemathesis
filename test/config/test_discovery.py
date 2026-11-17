import pytest

from schemathesis.config import SchemathesisConfig

TOML_CONTENT = """
color = true
suppress-health-check = ["too_slow"]
max-failures = 3
reports = { directory = "reports" }
"""


def test_discover_in_current_directory(tmp_path, monkeypatch):
    config_file = tmp_path / "schemathesis.toml"
    config_file.write_text(TOML_CONTENT)

    monkeypatch.chdir(tmp_path)

    config = SchemathesisConfig.discover()
    assert config.color is True
    assert config.suppress_health_check == ["too_slow"]
    assert config.max_failures == 3
    assert str(config.reports.directory) == "reports"


def test_discover_in_parent_directory(tmp_path, monkeypatch):
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


@pytest.mark.parametrize("git", ["directory", "file"], ids=["repository", "worktree-or-submodule"])
def test_discover_stops_at_git_root(tmp_path, monkeypatch, git):
    # A config above the repository root belongs to another project
    (tmp_path / "schemathesis.toml").write_text(TOML_CONTENT)
    repository = tmp_path / "repository"
    repository.mkdir()
    if git == "directory":
        (repository / ".git").mkdir()
    else:
        # Worktrees and submodules have a `.git` file pointing at the main repository
        (repository / ".git").write_text("gitdir: /elsewhere/.git/worktrees/repository\n")
    child = repository / "child"
    child.mkdir()

    monkeypatch.chdir(child)

    config = SchemathesisConfig.discover()
    assert (config.config_path, config.color) == (None, None)
