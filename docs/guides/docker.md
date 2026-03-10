# Using Schemathesis with Docker

The official Schemathesis Docker image lets you run tests without installing Python or managing dependencies.

## Basic Usage

```bash
docker run ghcr.io/schemathesis/schemathesis:stable \
  run -w auto https://api.example.com/openapi.json
```

!!! tip "Free-threaded Python"
    The image uses free-threaded Python (3.14t). `-w auto` lets Schemathesis use all available CPUs in parallel.

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

tracecov is pre-installed and active by default — the built-in `hooks.py` enables schema coverage tracking automatically. See [Schema Coverage — Docker](coverage.md#docker) for details, opt-out, and custom hooks patterns.

## Reports

Mount a directory to retrieve generated report files on the host:

```bash
docker run \
  -v ./schemathesis-report:/app/schemathesis-report \
  ghcr.io/schemathesis/schemathesis:stable \
  run -w auto --report junit https://api.example.com/openapi.json
```

`junit.xml` will appear in `./schemathesis-report/` on your host. See [CI/CD Integration](cicd.md) for how to consume this in GitHub Actions or GitLab CI.
