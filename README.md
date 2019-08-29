# Schemathesis

Schemathesis is a tool that generates test cases for your
Open API / Swagger schemas.

The main goal is to bring property-based testing to web applications and
verify if all values allowed by the schema are processed correctly
by the application.

Empowered by `Hypothesis`, `hypothesis_jsonschema` and `pytest`.

**NOTE**: The library is WIP, the API is a subject to change.

## Usage

To generate test cases for your schema you need:

- Create a parametrizer;
- Wrap a test with `Parametrizer.parametrize` method
- Provide a client and url of a running application instance

```python
import pytest
import requests
from schemathesis import Parametrizer


schema = Parametrizer.from_path("path/to/schema.yaml")

@pytest.fixture(scope="session")
def client():
    return requests.Session()

@schema.parametrize()
def test_users_endpoint(client, case):
    url = "http://0.0.0.0:8080" + case.formatted_path
    response = client.request(
        case.method,
        url,
        params=case.query,
        json=case.body
    )
    assert response.status_code == 200
```

Each wrapped test will have the `case` fixture, that represents a
hypothesis test case.

Case consists of:

- `method`
- `formatted_path`
- `query`
- `body`

For each `schemathesis` will create `hypothesis` strategies which will
generate bunch of random inputs acceptable by schema.
This data could be used to verify that your application works in the way
as described in the schema or that schema describes expected behaviour.

For example the data could be send against running app container via
`requests` and response is checked for an expected status code or error
message.

## Documentation

For full documentation, please see [https://schemathesis.readthedocs.io/en/latest/]

Or you can look at the [docs/] directory in the repository.

## Python support

Schemathesis supports Python 3.6, 3.7 and 3.8.

## License

The code in this project is licensed under [MIT license](https://opensource.org/licenses/MIT).
By contributing to `schemathesis`, you agree that your contributions
will be licensed under its MIT license.
