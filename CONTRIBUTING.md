# Contributing to Schemathesis

Thanks for helping out! This page covers issues, pull requests and docs changes.

## Reporting Bugs

Open an issue in the [issue tracker](https://github.com/schemathesis/schemathesis/issues) with:

- **Title**: the problem in one line.
- **Reproduction steps**: the schema (or a minimal part of it), the command you ran and any config.
- **Observed behavior**: what happened, with logs or the full error message.
- **Expected behavior**: what you expected instead, and why.
- **Versions**: Python and Schemathesis (`st --version`). Check that the bug still happens on the latest Schemathesis release.

## Suggesting Features

Open an [issue](https://github.com/schemathesis/schemathesis/issues) that describes:

- **Your use case**: what you are testing and where Schemathesis falls short.
- **The behavior you want**: how the feature would work from your side, e.g. a CLI flag or a config option.
- **Scope**: one concrete use case rather than a broad feature set.

I try to answer every issue within a few days. If you hear nothing, comment on the issue.

## Submitting Pull Requests

You need Python 3.10+, [uv](https://docs.astral.sh/uv/) and `curl`. The tests run the curl commands Schemathesis generates, so install `curl` with your OS package manager, e.g. `apt install curl` or `brew install curl`.

[`just`](https://github.com/casey/just) is optional. It wraps the commands below, and `just --list` shows them all.

1. Fork the repository and clone your fork.
2. Create a branch from `master`.
3. Install the development dependencies:

    ```bash
    uv venv --python 3.10    # any version from 3.10 up works
    source .venv/bin/activate    # fish: .venv/bin/activate.fish
    uv pip install -e ".[dev]"
    ```

    The `dev` extra includes test, lint and docs dependencies. Run every command below inside the activated venv.

4. Install the [prek](https://github.com/j178/prek) Git hooks. They run `ruff` and `mypy` on every commit:

    ```bash
    uvx prek install
    ```

5. Write a test that fails without your change, next to the existing tests for that area (`test/cli/` for CLI behavior, `test/specs/openapi/` for OpenAPI handling). CLI tests run the real `st run` command through the `cli` fixture and compare the output with the `snapshot_cli` fixture. Test servers come from `ctx.openapi.apps` (defined in `test/apps/catalog/`); see `test/cli/test_auth_bootstrap.py` for an example.
6. Run the tests and checks:

    ```bash
    just test-dist    # or: python -m pytest test/ -n auto --ignore=test/tooling --snapshot-warn-unused
    just check        # or: uvx prek run --all-files
    ```

    If your change alters CLI output, update the snapshots and review the diff:

    ```bash
    just snapshot-update    # or: python -m pytest test/ -n auto --ignore=test/tooling --snapshot-update
    ```

7. Add an entry to `CHANGELOG.md` under `Unreleased`, in the `Added`, `Changed` or `Fixed` section. Describe what users see in up to 15 words. Docs-only and test-only changes need no entry.
8. Write the commit message in [Conventional Commits](https://www.conventionalcommits.org/en/) format, with a capitalized description, e.g. `fix: Crash on schemas whose info is not an object` or `feat: Add the --request-retries option`.
9. Open the pull request against `master`.

I review pull requests within a few days. If you hear nothing, comment on the pull request.

## Contributing to Documentation

The docs live in `docs/` and build with MkDocs. Follow steps 1-3 of [Submitting Pull Requests](#submitting-pull-requests), then run:

```bash
mkdocs serve
```

Open the URL from the terminal output (usually `http://127.0.0.1:8000/`) to preview your changes.

## Community and Support

Ask questions and discuss ideas on [Discord](https://discord.gg/R9ASRAmHnA). Write in English, in issues and on Discord.

## Maintainers

- Dmitry Dygalo ([@Stranger6667](https://github.com/Stranger6667))
