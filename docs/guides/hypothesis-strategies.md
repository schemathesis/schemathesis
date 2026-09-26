# Using Hypothesis Strategies with Schemathesis

This guide shows how to combine Schemathesis with your own [Hypothesis](https://hypothesis.readthedocs.io/) strategies: injecting custom data into Schemathesis tests, and drawing Schemathesis test cases inside your own Hypothesis tests.

## Prerequisites

- Schemathesis and pytest installed
- Familiarity with Hypothesis strategies and `@given`

The examples load the schema from an ASGI app in `myapp.py` with `POST /users`, `GET`/`PUT`/`DELETE /users/{user_id}` and `POST /posts`. Any loader works the same way, for example `schemathesis.openapi.from_url(...)`.

## Turn API operations into strategies

Every API operation is a Hypothesis strategy that generates `Case` objects:

```python
import schemathesis

from myapp import app

schema = schemathesis.openapi.from_asgi("/openapi.json", app)

# Single operation strategy
create_user = schema["/users"]["POST"].as_strategy()
get_user = schema["/users/{user_id}"]["GET"].as_strategy()

# Multiple operations combined
user_operations = create_user | get_user

# All operations for a path
all_user_operations = schema["/users/{user_id}"].as_strategy()

# All operations in the schema
all_operations = schema.as_strategy()
```

A `Case` holds the method, path, headers, query parameters and request body needed to make a request. The strategies behave like any other Hypothesis strategy; see the [Hypothesis documentation](https://hypothesis.readthedocs.io/en/latest/data.html).

The snippets below assume these imports and the `schema` defined above:

```python
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
```

## Add custom data to Schemathesis tests

### Inject values with `@schema.given()`

`@schema.given()` works like Hypothesis's `@given` and combines with `@schema.parametrize()`:

```python
@schema.given(auth_token=st.sampled_from(["token1", "token2", "token3"]))
@schema.parametrize()
def test_api_with_auth(case, auth_token):
    case.headers["Authorization"] = f"Bearer {auth_token}"
    case.call_and_validate()


existing_user_ids = [1, 42, 123, 456]


@schema.given(user_id=st.sampled_from(existing_user_ids))
@schema.parametrize()
def test_user_endpoints(case, user_id):
    if "user_id" in case.path_parameters:
        case.path_parameters["user_id"] = user_id
    case.call_and_validate()
```

Each test runs many Hypothesis examples, so the custom values are sampled across different generated test cases.

!!! warning "Schema examples and `@schema.given()`"

    If your schema contains examples (in parameters or request bodies), a test that uses `@schema.given()` fails with `IncorrectUsage: Cannot combine @schema.given() with schema examples`. Schema examples only provide the `case` argument, and the extra arguments have no value.

    Split the test by Hypothesis phase:

    ```python
    from hypothesis import Phase, settings


    # Schema examples, without @schema.given()
    @schema.parametrize()
    @settings(phases=[Phase.explicit])
    def test_user_endpoints_with_examples(case):
        case.call_and_validate()


    # Generated cases with custom strategies
    @schema.given(user_id=st.sampled_from(existing_user_ids))
    @schema.parametrize()
    @settings(phases=[Phase.generate])
    def test_user_endpoints_with_custom_data(case, user_id):
        if "user_id" in case.path_parameters:
            case.path_parameters["user_id"] = user_id
        case.call_and_validate()
    ```

### Create and clean up records per case

`db` here is your own fixture that creates and deletes users. With pytest-django, override its `db` fixture in `conftest.py` so the helpers run with database access:

```python
import pytest

from users.models import User


class UserStore:
    def create_user(self, data):
        return User.objects.create(**data).id

    def delete_user(self, user_id):
        User.objects.filter(id=user_id).delete()


@pytest.fixture
def db(db):
    return UserStore()
```

The test then uses it:

```python
@schema.given(
    user_data=st.fixed_dictionaries(
        {
            "name": st.text(min_size=1, max_size=50),
            "role": st.sampled_from(["user", "admin"]),
        }
    )
)
@schema.parametrize()
@settings(suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_api_with_db_setup(db, case, user_data):
    user_id = db.create_user(user_data)
    try:
        if "user_id" in case.path_parameters:
            case.path_parameters["user_id"] = user_id
        case.call_and_validate()
    finally:
        db.delete_user(user_id)
```

A function-scoped fixture such as `db` is created once per test function, not once per generated case, so Hypothesis raises `FailedHealthCheck` unless you suppress `HealthCheck.function_scoped_fixture`. The `try`/`finally` block creates and removes a user for every generated case.

With Django, load the schema from the WSGI application, `schemathesis.openapi.from_wsgi("/openapi.json", get_wsgi_application())`. WSGI requests run in the test's thread and see the rows the fixture creates inside pytest-django's transaction. Django's ASGI application serves requests on another database connection, outside that transaction, and the test fails.

### Choose the next request from a response

`st.data()` lets the test draw more values while it runs. Here the response to `POST /users` decides which request follows:

```python
get_user_operation = schema["/users/{user_id}"]["GET"].as_strategy()
create_post_operation = schema["/posts"]["POST"].as_strategy()


@schema.given(data=st.data())
@schema.parametrize()
def test_user_workflow(case, data):
    response = case.call_and_validate()
    if case.method == "POST" and case.path == "/users" and response.status_code == 201:
        user = response.json()
        if user["role"] == "admin":
            next_case = data.draw(get_user_operation)
            next_case.path_parameters = {"user_id": user["id"]}
        else:
            next_case = data.draw(create_post_operation)
            next_case.body = {**next_case.body, "author_id": user["id"]}
        next_case.call_and_validate()
```

## Use Schemathesis strategies in your own tests

### Build a request sequence

With plain `@given`, you control the order of requests and Schemathesis generates the data for each step:

```python
create_user_strategy = schema["/users"]["POST"].as_strategy()
update_user_strategy = schema["/users/{user_id}"]["PUT"].as_strategy()
delete_user_strategy = schema["/users/{user_id}"]["DELETE"].as_strategy()


@given(data=st.data())
def test_user_lifecycle(data):
    # Step 1: always create a user
    create_case = data.draw(create_user_strategy)
    response = create_case.call_and_validate()
    user_id = response.json()["id"]

    # Step 2: optional steps
    if data.draw(st.booleans()):
        update_case = data.draw(update_user_strategy)
        update_case.path_parameters = {"user_id": user_id}
        update_case.call_and_validate()

    if data.draw(st.booleans()):
        post_case = data.draw(schema["/posts"]["POST"].as_strategy())
        post_case.body = {**post_case.body, "author_id": user_id}
        post_case.call_and_validate()

    # Step 3: always clean up
    delete_case = data.draw(delete_user_strategy)
    delete_case.path_parameters = {"user_id": user_id}
    delete_case.call_and_validate()
```

### Use strategies with `unittest`

Strategies work with `@given` in any test framework that supports Hypothesis:

```python
from unittest import TestCase

from hypothesis import given

create_user_strategy = schema["/users"]["POST"].as_strategy()
get_user_strategy = schema["/users/{user_id}"]["GET"].as_strategy()


class TestAPI(TestCase):
    @given(case=create_user_strategy)
    def test_create_user(self, case):
        response = case.call_and_validate()
        self.assertIn("id", response.json())

    @given(create_case=create_user_strategy, get_case=get_user_strategy)
    def test_create_then_get(self, create_case, get_case):
        create_response = create_case.call_and_validate()
        user_id = create_response.json()["id"]

        get_case.path_parameters = {"user_id": user_id}
        get_response = get_case.call_and_validate()

        self.assertEqual(get_response.json()["id"], user_id)
```

Run these tests with `pytest` or `python -m unittest`:

```
..
----------------------------------------------------------------------
Ran 2 tests in 0.717s

OK
```

## Troubleshooting

**`FailedHealthCheck: '...' uses a function-scoped fixture '...'.`** Suppress `HealthCheck.function_scoped_fixture` as shown above, or give the fixture a wider scope.

**``IncorrectUsage: Cannot combine `@schema.given()` with schema examples.``** Split the test into an explicit-examples test and a generated-data test, as shown in the warning above.

**``OperationNotFound: `/users/{id}` not found. Did you mean `/users/{user_id}`?``** The path in `schema["/path"]["METHOD"]` must match the schema exactly, including path parameter names such as `{user_id}`. An unknown method raises ``LookupError: Method `POST` not found. Available methods: GET``.
