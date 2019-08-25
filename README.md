# Schemathesis

Schemathesis is a tool that generates test cases for your Open API / Swagger schemas.

The main goal is to verify if all values allowed by the schema are processed correctly
by the application.

Empowered with `Hypothesis`, `hypothesis_jsonschema` and `pytest`.

**NOTE**: The library is WIP, the API is a subject to change.

## Usage

To generate test cases for your schema you need:

- Create a parametrizer;
- Wrap a test with `Parametrizer.parametrize` method

```python
from schemathesis import Parametrizer


schema = Parametrizer.from_path("path/to/schema.yaml")


@schema.parametrize()
def test_users_endpoint(client, case):
    response = client.request(
        case.method, 
        case.formatted_path,
        params=case.query,
        json=case.body
    )
    assert response.status_code == 200
```

Each wrapped test will have the `case` fixture, that represents a hypothesis test case.

Case consists of:

- `method`
- `formatted_path`
- `query`
- `body`

This data could be used to verify that your application works in the way as described in the schema.
For example the data could be send against running app container via `requests` and response is checked
for an expected status code or error message.

