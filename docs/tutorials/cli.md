# Schemathesis CLI Tutorial

**Estimated time: 15-20 minutes**

This tutorial walks you through a complete API testing workflow with Schemathesis using a booking API. You'll see how property-based testing automatically finds bugs that manual testing typically misses.

If you're new to Schemathesis, check the [Quick Start Guide](../quick-start.md) first.

## Prerequisites

 - **[Git](https://git-scm.com/downloads){target=_blank}** - to clone the example API repository

 - **[Docker Compose](https://docs.docker.com/get-docker/){target=_blank}** - Install Docker Desktop which includes Docker Compose

 - **[uv](https://docs.astral.sh/uv/getting-started/installation/){target=_blank}** - Python package manager that allows running Schemathesis without installation using `uvx`

 - **[curl](https://curl.se/download.html){target=_blank}** (optional) - for reproducing API failures manually

**Verify your setup:**

```console
git --version
docker compose version
uv --version
curl --version  # optional
```

## API under test

We'll test a booking API that handles hotel reservations - creating bookings and retrieving guest information.

The API lives in the [Schemathesis repository](https://github.com/schemathesis/schemathesis/tree/master/examples/booking):

```console
git clone https://github.com/schemathesis/schemathesis.git
cd schemathesis/examples/basic
docker compose up -d
```
!!! success "Verify the API is running"

    Open [http://localhost:8080/docs](http://localhost:8080/docs){target=_blank} - you should see the interactive API documentation.

!!! note "Authentication token"

    The API requires bearer token authentication. Use: `secret-token`

## First Test Run

Run Schemathesis against the API:

```bash
uvx schemathesis run http://127.0.0.1:8080/openapi.json \
  --header 'Authorization: Bearer secret-token' \
  --output-sanitize false
```

**Key findings from the output:**

!!! failure "Bug discovered"
    ```
    ❌ Server error: 1
    ❌ Undocumented HTTP status code: 1
    
    Reproduce with:
    curl -X POST -H 'Authorization: Bearer secret-token' \
      -H 'Content-Type: application/json' \
      -d '{"guest_name": "00", "nights": 1, "room_type": ""}' \
      http://127.0.0.1:8080/bookings
    ```

Empty `room_type` causes a 500 error because the pricing logic can't handle unexpected values.

Run the provided `curl` command to reproduce this failure.

## Reporting

Schemathesis can export test results in multiple formats for integration with existing tools and CI/CD pipelines. Let's generate a JUnit report that can be imported into Jenkins, GitLab CI, or other test management systems:

```bash
uvx schemathesis run http://127.0.0.1:8080/openapi.json \
  --header 'Authorization: Bearer secret-token' \
  --output-sanitize false \
  --report junit
```

This creates a `junit.xml` file in the `schemathesis-report` directory containing structured test results. The JUnit format includes:

- Test case details and execution times
- Failure descriptions with reproduction steps

You can customize the output location using `--report-junit-path` or change the report directory with `--report-dir`. Other available formats include VCR cassettes (`--report vcr`) and HAR files (`--report har`) for detailed HTTP traffic analysis.

## Fixing the bug

!!! bug "Root cause"
    The schema allows any string for `room_type`, but our pricing logic only handles specific values.


**Fix: Constrain room types in the schema**

=== "Before (broken)"
    ```python
    class BookingRequest(BaseModel):
        room_type: str  # Any string allowed!
    ```

=== "After (fixed)"
    ```python
    from enum import Enum
    
    class RoomType(str, Enum):
        standard = "standard"
        deluxe = "deluxe" 
        suite = "suite"
    
    class BookingRequest(BaseModel):
        room_type: RoomType  # Only valid values
    ```

**Don't forget to restart:**

```bash
docker compose restart
```

## Re-running the tests

Now let's verify our fix by re-running the tests. Focus on the operation you just fixed:

```bash
uvx schemathesis run http://127.0.0.1:8080/openapi.json \
  --header 'Authorization: Bearer secret-token' \
  --output-sanitize false \
  --include-operation-id create_booking_bookings_post
```

!!! info "Operation ID explained"
    FastAPI generates operation IDs automatically. You can find them in the OpenAPI schema or API docs.

The `--include-operation-id` option targets only the specific operation we fixed, making the test run faster during development. You can also filter by HTTP method (`--include-method POST`) or path patterns (`--include-path /bookings`).

## Generating more test cases

!!! question "Want to find more bugs?"
    By default, Schemathesis stops at the first failure per operation and runs a limited number of test cases.

Let's be more thorough:

```bash
uvx schemathesis run http://127.0.0.1:8080/openapi.json \
  --header 'Authorization: Bearer secret-token' \
  --output-sanitize false \
  --max-examples 500 \
  --continue-on-failure
```

**`--max-examples 500`** generates more test cases per operation, increasing the chance of finding edge cases that smaller test runs might miss.

**`--continue-on-failure`** prevents Schemathesis from stopping at the first failure, allowing it to discover multiple issues on the same API operation in a single run.

These options are particularly valuable when:

- Preparing for production releases
- Testing complex validation logic

The trade-off is longer execution time, but you'll get more chances to find bugs.

## Configuration File

!!! question "Tired of long command lines?"
    Instead of repeating long commands, save your settings once and reuse them across your team.

**Create `schemathesis.toml` in your project:**

```toml
# Core settings from our previous commands
headers = { Authorization = "Bearer ${API_TOKEN}" }
continue-on-failure = true

[output.sanitization]
enabled = false

[generation] 
max-examples = 500

[reports.junit]
enabled = true
```

!!! tip "Environment variables"
    ```bash
    export API_TOKEN=secret-token
    ```

**Now run with just:**
```bash
uvx schemathesis run http://127.0.0.1:8080/openapi.json
```

Schemathesis automatically loads `schemathesis.toml` from the current directory or project root. You can specify a custom configuration file:

```bash
uvx schemathesis --config-file path/to/config.toml run http://...
```

!!! info "Configuration precedence"
    CLI options override config file settings, so you can still adjust settings temporarily.

## What's next?

**Continue learning:**

- **[CLI Reference](../reference/cli.md)** - All available CLI options
- **[Configuration Reference](../reference/configuration.md)** - Complete configuration reference
