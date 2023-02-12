"""Simple CLI for changelog management."""
import argparse
import datetime
import re
import sys
from typing import Generator, List, Optional

PYPROJECT_PATH = "pyproject.toml"
CHANGELOG_PATH = "docs/changelog.rst"
COMPARE_URL_PREFIX = "https://github.com/schemathesis/schemathesis/compare/"


def _read_changelog() -> List[str]:
    with open(CHANGELOG_PATH) as f:
        return f.readlines()


def _find_line_by_prefix(lines: List[str], prefix: str) -> Optional[int]:
    return next((i for i, line in enumerate(lines) if line.startswith(prefix)), None)


def bump(new_version: str) -> None:
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    # Read changelog into lines
    changelog = _read_changelog()

    # Find the position of the "Unreleased" block
    unreleased_idx = _find_line_by_prefix(changelog, "`Unreleased`_ -")
    if unreleased_idx is None:
        raise RuntimeError("Changelog has no `Unreleased` section")

    # Place to insert the new release block
    new_version_idx = unreleased_idx + 3

    if changelog[new_version_idx].startswith(".. _v"):
        raise RuntimeError("New version has no changes")

    # Insert the new release block before the "Unreleased" block
    new_version_link = f".. _v{new_version}:\n\n"
    new_version_line = f"`{new_version}`_ - {today}"
    new_version_underline = f"\n{'-' * len(new_version_line)}\n\n"
    changelog.insert(new_version_idx, f"{new_version_link}{new_version_line}{new_version_underline}")

    # Find the position of the link for the "Unreleased" diff & rewrite it with the new version
    unreleased_diff_idx = _find_line_by_prefix(changelog, f".. _Unreleased: {COMPARE_URL_PREFIX}")
    if unreleased_diff_idx is None:
        raise RuntimeError("Changelog has no diff for the `Unreleased` section")
    changelog[unreleased_diff_idx] = f".. _Unreleased: {COMPARE_URL_PREFIX}v{new_version}...HEAD\n"

    # Extract the old version from the next line
    # `.. _3.18.2: ...` => `3.18.2`
    old_version_diff_idx = unreleased_diff_idx + 1
    old_version = changelog[old_version_diff_idx].split(":")[0][4:]
    # Insert the diff for the new version
    new_version_diff = f".. _{new_version}: {COMPARE_URL_PREFIX}v{old_version}...v{new_version}\n"
    changelog.insert(old_version_diff_idx, new_version_diff)

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


# Matches strings that look like "`#123`_"
GITHUB_LINK_RE = re.compile(r"`#([0-9]+)`_")


def clean_line(text: str) -> str:
    return GITHUB_LINK_RE.sub(lambda m: m.group().strip("`_"), text).replace("``", "`")


def _rst_to_md(lines: List[str]) -> Generator[str, None, None]:
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
