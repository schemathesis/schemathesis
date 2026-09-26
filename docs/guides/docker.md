# Using Schemathesis with Docker

This guide shows how to run Schemathesis from the official Docker image, without installing Python.

## Prerequisites

- Docker installed and running
- An API with an OpenAPI schema, reachable from inside the container

## Basic Usage

```bash
docker run ghcr.io/schemathesis/schemathesis:stable \
  run -w auto https://api.example.com/openapi.json
```

!!! tip "Free-threaded Python"
    The image uses free-threaded Python (3.14t). `-w auto` lets Schemathesis use all available CPUs in parallel.

## Image Tags

| Tag | Points at |
|---|---|
| `stable` | The newest stable release. Pre-releases do not move it. |
| `X.Y.Z` | That exact release. Immutable. |
| `latest` | Unreleased `master`, rebuilt on every push. |

Add the `-trixie` suffix (`stable-trixie`, `latest-trixie`) for the Debian trixie variant. All tags are published to both `ghcr.io/schemathesis/schemathesis` and `schemathesis/schemathesis` on Docker Hub.

Pin `X.Y.Z` in CI if you need reproducible runs; use `latest` only to try unreleased changes.

## File-Based Schema

Mount your local schema file into the container and point at your running API:

```bash
docker run \
  -v ./openapi.json:/app/openapi.json \
  ghcr.io/schemathesis/schemathesis:stable \
  run -w auto /app/openapi.json --url http://host.docker.internal:8080
```

`host.docker.internal` resolves to your host machine from inside the container (Docker Desktop). On Linux, use `--network host` instead and reference `localhost` directly.

## Hooks

A `hooks.py` file is how you customise Schemathesis — adding authentication, supplying realistic test data, filtering edge cases, or enabling coverage tracking. The image already has `SCHEMATHESIS_HOOKS=/app/hooks.py` set, so mounting your file there is all you need:

```bash
docker run \
  -v ./hooks.py:/app/hooks.py \
  ghcr.io/schemathesis/schemathesis:stable \
  run -w auto https://api.example.com/openapi.json
```

See [Extending Schemathesis](extending.md) for the full list of available hooks.

TraceCov is pre-installed and active by default — the built-in `hooks.py` enables schema coverage tracking automatically. See [Schema Coverage — Docker](coverage.md#docker) for details, opt-out, and custom hooks patterns.

## Reports

The container runs as a non-root user (`schemathesis`, UID 1000). If a mounted host directory does not exist, Docker creates it owned by `root` and Schemathesis cannot write to it. Create the directory before mounting it:

```bash
mkdir -p schemathesis-report
docker run \
  -v ./schemathesis-report:/app/schemathesis-report \
  ghcr.io/schemathesis/schemathesis:stable \
  run -w auto --report junit https://api.example.com/openapi.json
```

If your host UID (`id -u`) is not 1000, also make the directory writable for the container user with `chmod a+w schemathesis-report`.

A timestamped file such as `junit-20260925T091608Z.xml` appears in `./schemathesis-report/` on your host. See [CI/CD Integration](cicd.md) for how to consume it in GitHub Actions or GitLab CI.

For Allure reports, Allure support is pre-installed in the image. Create and mount a directory the same way and use `--report-allure-path`:

```bash
mkdir -p allure-results
docker run \
  -v ./allure-results:/app/allure-results \
  ghcr.io/schemathesis/schemathesis:stable \
  run -w auto --report-allure-path /app/allure-results https://api.example.com/openapi.json
```

Then run the Allure CLI on your host to generate the HTML report. See [Allure Integration](allure.md) for details.

## Troubleshooting

**`Could not open file 'junit-....xml': Permission denied`.** The mounted report directory is not writable by the container user; prepare it as shown in [Reports](#reports). If Docker already created it as `root`, remove it (`sudo rm -r schemathesis-report`) first. Setting `--user` to another UID does not help: that user cannot write to `/app`, where Schemathesis keeps its working files.

**Connection refused when the API runs on the host.** `localhost` inside the container is the container itself. Use `host.docker.internal` (Docker Desktop) or `--network host` (Linux).

**The hooks file is not loaded.** Mount it at `/app/hooks.py`, or set `-e SCHEMATHESIS_HOOKS=/path/in/container.py` to the path you mounted it at.
