"""Simple CLI for changelog management."""

from __future__ import annotations

import argparse
import datetime
import re
import sys
from typing import Generator

PYPROJECT_PATH = "pyproject.toml"
CHANGELOG_PATH = "docs/changelog.rst"
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

    # Find the position of the "Unreleased" block
    unreleased_idx = _find_line_by_prefix(changelog, ":version:`Unreleased")
    if unreleased_idx is None:
        raise RuntimeError("Changelog has no `Unreleased` section")

    # Extract the old version
    old_version = changelog[unreleased_idx].split("<")[1].split("...")[0][1:]

    # Replace it with the new version
    unreleased_line = f":version:`Unreleased <v{new_version}...HEAD>` - TBD"
    changelog[unreleased_idx] = f"{unreleased_line}\n"
    changelog[unreleased_idx + 1] = "-" * len(unreleased_line) + "\n"

    # Place to insert the new release block
    new_version_idx = unreleased_idx + 3

    if changelog[new_version_idx].startswith(".. _v"):
        raise RuntimeError("New version has no changes")

    # Insert the new release block after the "Unreleased" block
    new_version_link = f".. _v{new_version}:\n\n"
    new_version_line = f":version:`{new_version} <v{old_version}...v{new_version}>` - {today}"
    new_version_underline = f"\n{'-' * len(new_version_line)}\n\n"
    changelog.insert(new_version_idx, f"{new_version_link}{new_version_line}{new_version_underline}")

    # Write the updated changelog back to the file
    with open(CHANGELOG_PATH, "w") as f:
        f.writelines(changelog)

    # Update `pyproject.toml`
    with open(PYPROJECT_PATH) as f:
        pyproject = f.readlines()

    version_idx = _find_line_by_prefix(pyproject, f'version = "{old_version}"')
    if version_idx is None:
        raise RuntimeError("`pyproject.toml` has no `version` field")

    pyproject[version_idx] = f'version = "{new_version}"\n'

    with open(PYPROJECT_PATH, "w") as f:
        f.writelines(pyproject)


def to_markdown(version: str) -> None:
    changelog = _read_changelog()
    # Find the start and end lines for the provided version
    start_idx = _find_line_by_prefix(changelog, f".. _v{version}")
    if start_idx is None:
        raise RuntimeError(f"Changelog misses the {version} version")
    start_idx += 4  # Skip the version link + version line and its underline
    end_idx = _find_line_by_prefix(changelog[start_idx + 1 :], ".. _v")
    if end_idx is None:
        raise RuntimeError("Changelog is missing the previous version")
    md_lines = _rst_to_md(changelog[start_idx : end_idx + start_idx])
    sys.stdout.write("\n".join(md_lines))
    sys.stdout.write("\n")


def _format_section(section: str) -> str:
    emoji = {
        "Added": "rocket",
        "Changed": "wrench",
        "Deprecated": "wastebasket",
        "Fixed": "bug",
        "Performance": "racing_car",
        "Removed": "fire",
    }.get(section, "wrench")
    return f"\n### :{emoji}: {section}\n"


# Matches strings that look like ":issue:`1890`"
GITHUB_LINK_RE = re.compile(r":issue:`([0-9]+)`")


def clean_line(text: str) -> str:
    return GITHUB_LINK_RE.sub(r"#\1", text).replace("``", "`")


def _rst_to_md(lines: list[str]) -> Generator[str, None, None]:
    for line in lines:
        line = line.strip()
        if line.startswith("**"):
            section = line.strip("*")
            yield _format_section(section)
        elif line:
            yield clean_line(line)


def build_parser() -> argparse.ArgumentParser:
    argument_parser = argparse.ArgumentParser(description="Manage Schemathesis changelog.")
    subparsers = argument_parser.add_subparsers(title="subcommands", dest="subcommand")

    # `bump` subcommand
    bump_parser = subparsers.add_parser("bump", help="Bump the version of the changelog")
    bump_parser.add_argument("new_version", type=str, help="The new version number to bump to")

    # `md` subcommand
    md_parser = subparsers.add_parser("md", help="Transform the changelog for a specific version into markdown style")
    md_parser.add_argument("version", type=str, help="The version to transform into markdown")

    return argument_parser


if __name__ == "__main__":
    parser = build_parser()
    args = parser.parse_args()
    if args.subcommand == "bump":
        bump(args.new_version)
    elif args.subcommand == "md":
        to_markdown(args.version)
    else:
        parser.error("Missing subcommand")
