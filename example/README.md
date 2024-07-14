# Demo Project

**Welcome to the Demo project!**

This project is designed to enhance your understanding of Schemathesis.
Building on the concepts covered in the Schemathesis documentation, this project will show practical applications, demonstrating how to integrate Schemathesis in both CLI and Python test suite environments.
You'll find specific use cases and code examples, helping you to apply them in your own projects.

The demo uses [Docker Compose](https://docs.docker.com/compose/) to run an API built with [Flask](https://flask.palletsprojects.com/en/3.0.x/) and [SQLAlchemy](https://www.sqlalchemy.org/).
While this setup is part of the demo environment, understanding the basics of Docker and Python will help you get the most out of this example.

We encourage you to experiment with the code and adapt it to fit your needs.
If you have questions or feedback, feel free to contribute to the discussion — your insights are invaluable to the Schemathesis community.

## Getting started

In this demo, you'll learn to run Schemathesis tests using either its CLI or [pytest](https://docs.pytest.org/en/7.4.x/), a widely-used testing framework in Python.

First, ensure that Docker Compose is installed on your machine. If you don’t have it, you can find detailed installation instructions on the [Docker Compose official website](https://docs.docker.com/compose/install/).
To verify your installation, run `docker compose version` in your terminal and confirm that you have version `2` or higher.

Next, you'll need to fork and clone the project's repository. This step is crucial as it allows you to have a local copy of the project to work with.

1. **Fork the Repository**: Create a [fork](https://docs.github.com/en/get-started/quickstart/fork-a-repo#forking-a-repository) of the [Schemathesis repository](https://github.com/schemathesis/schemathesis) on your GitHub account.

2. **Clone the Repository**: [Clone](https://docs.github.com/en/get-started/quickstart/fork-a-repo#cloning-your-forked-repository) your fork to your local machine for direct access to the app.

3. **Run the App Locally**: Execute `docker compose up` in your terminal to start the app. Once running, access the API UI at http://127.0.0.1:5123/ui/ to see all the available endpoints.

After setting up Docker and obtaining a copy of the project, you'll be ready to dive into the tests.

## Command-line

The examples below will include the following environment variables and a shell alias to avoid visual clutter:

```shell
export SCHEMA_URL="http://127.0.0.1:5123/openapi.json"
alias schemathesis-docker='docker run --rm -it -e SCHEMATHESIS_HOOKS=tests.extensions --network="host" -v $(pwd):/app schemathesis/schemathesis:stable'
```

Among other things, this alias enables custom extensions in Schemathesis by making the `tests/extensions.py` file in the current directory visible to Schemathesis CLI inside the container.

### Default run

Runs tests for all API operations with the default set of checks:

```shell
schemathesis-docker run $SCHEMA_URL
```

### All available checks

```shell
schemathesis-docker run --checks all $SCHEMA_URL
```

### Narrowing the testing scope

Only `POST` operations with paths starting with `/internal`:

```shell
schemathesis-docker run --include-method POST --include-path-regex '^/internal' $SCHEMA_URL
```

### Verifying responses with a custom check

```shell
schemathesis-docker run -c not_so_slow $SCHEMA_URL
```

### Custom headers

```shell
schemathesis-docker run -H 'Authorization: Bearer SECRET' $SCHEMA_URL
```

### Generating more examples per operation

Run up to 1000 examples per tested API operation

```shell
schemathesis-docker run --hypothesis-max-examples 1000 $SCHEMA_URL
```

**NOTE**: This parameter caps the number of generated test cases for the main testing phase, excluding test case minimization and verification.

### Running in multiple threads

```shell
schemathesis-docker run -w 8 $SCHEMA_URL
```

### Store test log to a file

```shell
schemathesis-docker run --cassette-path=cassette.yaml $SCHEMA_URL
```

### Replay the test log

```shell
schemathesis-docker replay cassette.yaml
```

# Python tests

Install dependencies:

```shell
python -m pip install -r requirements-pytest.txt
```

Run `pytest`:

```shell
pytest tests --tb=short
```

These tests include:

- A unit test & an integration test;
- Custom hypothesis settings;
- Using `pytest` fixtures;
- Providing a custom authorization header;
- Custom strategy for Open API string format;
- A hook for filtering generated data;
- Custom response check;

See the details in the `/tests` directory.
