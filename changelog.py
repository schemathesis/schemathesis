"""Simple CLI for changelog management."""

from __future__ import annotations

import argparse
import datetime
import sys

PYPROJECT_PATH = "pyproject.toml"
CHANGELOG_PATH = "CHANGELOG.md"
COMPARE_URL_PREFIX = "https://github.com/schemathesis/schemathesis/compare/"


def _read_changelog() -> list[str]:
    with open(CHANGELOG_PATH) as f:
        return f.readlines()


def _find_line_by_prefix(lines: list[str], prefix: str) -> int | None:
    return next((i for i, line in enumerate(lines) if line.startswith(prefix)), None)


def bump(new_version: str) -> None:
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    # Read changelog into lines
    changelog = _read_changelog()

    # Find the "Unreleased" block
    unreleased_idx = _find_line_by_prefix(changelog, "## [Unreleased](")
    if unreleased_idx is None:
        raise RuntimeError("Changelog has no 'Unreleased' section")

    # Extract the old version from the compare URL.
    old_version = (
        changelog[unreleased_idx].split("https://github.com/schemathesis/schemathesis/compare/")[1].split("...")[0][1:]
    )

    # Replace the unreleased header with the new compare URL.
    unreleased_line = f"## [Unreleased]({COMPARE_URL_PREFIX}v{new_version}...HEAD) - TBD\n"
    changelog[unreleased_idx] = unreleased_line

    # Determine where to insert the new release block (immediately after the unreleased header).
    new_version_idx = unreleased_idx + 2

    # Check if the new version already exists.
    if new_version_idx < len(changelog) and changelog[new_version_idx].startswith("## ["):
        raise RuntimeError("New version already exists or no changes to release")

    new_version_line = f"## [{new_version}]({COMPARE_URL_PREFIX}v{old_version}...v{new_version}) - {today}\n\n"
    changelog.insert(new_version_idx, new_version_line)

    # Write the updated changelog back to the file.
    with open(CHANGELOG_PATH, "w") as f:
        f.writelines(changelog)

    with open(PYPROJECT_PATH) as f:
        pyproject = f.readlines()

    version_idx = _find_line_by_prefix(pyproject, f'version = "{old_version}"')
    if version_idx is None:
        raise RuntimeError("`pyproject.toml` has no `version` field")

    pyproject[version_idx] = f'version = "{new_version}"\n'

    with open(PYPROJECT_PATH, "w") as f:
        f.writelines(pyproject)


def notes(version: str) -> None:
    changelog = _read_changelog()
    # Find the release header for the provided version
    start_idx = _find_line_by_prefix(changelog, f"## [{version}](")
    if start_idx is None:
        raise RuntimeError(f"Changelog misses the {version} version")
    # Determine end of release block by finding the next header
    end_idx = next(
        (i for i, line in enumerate(changelog[start_idx + 1 :], start=start_idx + 1) if line.startswith("## [")),
        len(changelog),
    )
    sys.stdout.write("".join(changelog[start_idx + 1 : end_idx]))


def build_parser() -> argparse.ArgumentParser:
    argument_parser = argparse.ArgumentParser(description="Manage Schemathesis changelog.")
    subparsers = argument_parser.add_subparsers(title="subcommands", dest="subcommand")

    bump_parser = subparsers.add_parser("bump", help="Bump the version of the changelog")
    bump_parser.add_argument("new_version", type=str, help="The new version number to bump to")

    notes = subparsers.add_parser("notes", help="Output the changelog for a specific version")
    notes.add_argument("version", type=str, help="The version to output a changelog")

    return argument_parser


if __name__ == "__main__":
    parser = build_parser()
    args = parser.parse_args()
    if args.subcommand == "bump":
        bump(args.new_version)
    elif args.subcommand == "notes":
        notes(args.version)
    else:
        parser.error("Missing subcommand")
