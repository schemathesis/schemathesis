default:
    @just --list

# Quick aliases
alias c := check
alias f := fmt
alias t := test
alias td := test-dist
alias tc := test-cov
alias tch := test-cov-html

# Run all tests
test *ARGS:
    python -m pytest test/ {{ARGS}}

test-dist *ARGS:
    python -m pytest test/ -n auto --dist=worksteal {{ARGS}}

# Run tests with coverage
test-cov *ARGS:
    @rm -f .coverage*
    COVERAGE_PROCESS_START=pyproject.toml coverage run -m pytest test/ -n auto --dist=worksteal {{ARGS}} || true
    coverage combine
    coverage report

# Run tests with coverage and open HTML report
test-cov-html *ARGS:
    @just test-cov {{ARGS}}
    coverage html
    @xdg-open htmlcov/index.html

# Run tests matching pattern
test-k PATTERN *ARGS:
    python -m pytest test/ -k "{{PATTERN}}" {{ARGS}}

# Update test snapshots
snapshot-update *ARGS:
    python -m pytest test/ --snapshot-update -n auto --dist=worksteal {{ARGS}}

# Run corpus tests
test-corpus:
    python -m pytest test-corpus/ -n auto --dist=worksteal

check:
    uvx prek run --all-files

fmt:
    uvx prek run ruff-format --all-files

lint:
    uvx prek run ruff-check --all-files

fix:
    uvx ruff check --fix src/ test/

typecheck:
    uvx prek run mypy --all-files

clean-cov:
    rm -f .coverage* coverage.xml
    rm -rf htmlcov/

clean: clean-cov
    rm -rf .pytest_cache .hypothesis .mypy_cache .ruff_cache
    rm -rf build/ dist/ *.egg-info
    find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
    find . -type f -name "*.pyc" -delete

docs:
    mkdocs serve

install:
    uv pip install -e ".[dev]"

hooks:
    uvx prek install

setup: install hooks

release VERSION:
    python changelog.py bump {{VERSION}}
    git add CHANGELOG.md pyproject.toml
    git commit -s -m "chore: Release {{VERSION}}"
    git tag -s v{{VERSION}} -m "Release {{VERSION}}"
    git push origin master
    git push origin v{{VERSION}}
    @echo "✓ Released {{VERSION}}"
