# Using Hypothesis Strategies with Schemathesis

Schemathesis is built on top of Hypothesis, which means every API operation becomes a Hypothesis strategy that generates `Case` objects. 

This foundation enables two powerful patterns: enhancing Schemathesis tests with custom data generation, and using Schemathesis strategies in custom testing workflows.

## Foundation: Schemathesis API Operations as Strategies

Every API operation in your schema can be converted to a Hypothesis strategy:

```python
import schemathesis

schema = schemathesis.openapi.from_url("http://api.example.com/openapi.json")

# Single operation strategy
create_user = schema["/users"]["POST"].as_strategy()
get_user = schema["/users/{id}"]["GET"].as_strategy()

# Multiple operations combined
user_operations = create_user | get_user

# All operations for a path
all_user_operations = schema["/users"].as_strategy()

# All operations in the schema
all_operations = schema.as_strategy()
```

These strategies generate `Case` objects containing HTTP method, path, headers, query parameters, and request body - everything needed to make an API request. They behave like any other Hypothesis strategy. 

!!! tip "Read More"
    For detailed information about working with strategies, see the [Hypothesis documentation](https://hypothesis.readthedocs.io/en/latest/data.html).

## Adding Custom Strategies to Schemathesis Tests

### Simple Data Injection

Use `@schema.given()` to inject custom data into your Schemathesis tests. This works like Hypothesis's `@given` decorator but integrates with Schemathesis's parametrization:

```python
from hypothesis import strategies as st

# Generate authentication tokens
@schema.given(auth_token=st.sampled_from(["token1", "token2", "token3"]))
@schema.parametrize()
def test_api_with_auth(case, auth_token):
    case.headers["Authorization"] = f"Bearer {auth_token}"
    case.call_and_validate()

# Use existing data for path parameters
existing_user_ids = [1, 42, 123, 456]

@schema.given(user_id=st.sampled_from(existing_user_ids))
@schema.parametrize() 
def test_user_endpoints(case, user_id):
    if "user_id" in case.path_parameters:
        case.path_parameters["user_id"] = user_id
    case.call_and_validate()
```

Each test will run multiple Hypothesis examples, so your custom data will be sampled repeatedly across different generated test cases.

!!! warning "Schema Examples and @schema.given()"

    If your schema contains examples (in parameters or request bodies), you cannot use `@schema.given()` with custom strategies on the same test function. Schema examples only provide the `case` parameter, while custom strategies require additional parameters, creating a parameter mismatch.

    **Solution**: Create separate test functions with different phases:

    ```python
    from hypothesis import Phase, settings

    # 1. One for schema examples (without @schema.given()):
    @schema.parametrize()
    @settings(phases=[Phase.explicit])
    def test_user_endpoints_with_examples(case):
        case.call_and_validate()

    # 2. One for property-based testing with your custom strategies:
    @schema.given(user_id=st.sampled_from(existing_user_ids))
    @schema.parametrize()
    @settings(phases=[Phase.generate])
    def test_user_endpoints_with_custom_data(case, user_id):
        if "user_id" in case.path_parameters:
            case.path_parameters["user_id"] = user_id
        case.call_and_validate()
    ```

### Database Setup with Cleanup

```python
@schema.given(
    user_data=st.fixed_dictionaries(
        {
            "name": st.text(min_size=1, max_size=50),
            "email": st.emails(),
            "role": st.sampled_from(["user", "admin"]),
        }
    )
)
@schema.parametrize()
def test_api_with_db_setup(db, case, user_data):
    # Create user in database for each Hypothesis example
    user_id = db.create_user(user_data)
    try:
        # Use the created user in API tests
        if "user_id" in case.path_parameters:
            case.path_parameters["user_id"] = user_id
        case.call_and_validate()
    finally:
        # Important: cleanup after each example
        db.cleanup_user(user_id)
```

Since Hypothesis generates multiple examples, a new user is created and cleaned up for each test case. Proper cleanup is essential to avoid test pollution.

### Dynamic Endpoint Selection Based on Results  

```python
# Define operation strategies for different scenarios
admin_operations = schema["/admin"].as_strategy()
regular_operations = schema["/posts"].as_strategy()

@schema.given(data=st.data())
@schema.parametrize()
def test_user_workflow(case, data):
    if case.method == "POST" and case.path == "/users":
        # Let Schemathesis generate and execute user creation
        response = case.call_and_validate()
        user_data = response.json()

        # Choose next operations based on what was created
        if user_data.get("role") == "admin":
            # Test admin-specific endpoints
            admin_case = data.draw(admin_operations)
            admin_case.headers["User-ID"] = str(user_data["id"])
            admin_case.call_and_validate()
        else:
            # Test regular user endpoints  
            user_case = data.draw(regular_operations)
            user_case.headers["User-ID"] = str(user_data["id"])
            user_case.call_and_validate()
    else:
        # For other operations, test normally
        case.call_and_validate()
```

The `st.data()` strategy lets you draw additional values during test execution, enabling dynamic decision-making based on API responses. This pattern lets you build workflows where Schemathesis handles initial data generation, and you make decisions about subsequent testing based on actual results.

## Using Schemathesis Strategies Elsewhere

### Custom Stateful Testing with Dynamic Steps

```python
from hypothesis import given, strategies as st

# Define operation strategies
create_user_strategy = schema["/users"]["POST"].as_strategy()
update_user_strategy = schema["/users/{id}"]["PUT"].as_strategy()
delete_user_strategy = schema["/users/{id}"]["DELETE"].as_strategy()

@given(data=st.data())
def test_user_lifecycle(data):
    # Step 1: Always create user
    create_case = data.draw(create_user_strategy)
    response = create_case.call_and_validate()
    user_id = response.json()["id"]

    # Step 2: Probabilistic operations
    if data.draw(st.integers(min_value=0, max_value=10)) < 7:  # 70% chance
        # Update user
        update_case = data.draw(update_user_strategy)
        update_case.path_parameters = {"id": user_id}
        update_case.call_and_validate()
    
    if data.draw(st.booleans()):  # 50% chance
        # Create a post for this user
        post_case = data.draw(schema["/posts"]["POST"].as_strategy())
        post_case.body["author_id"] = user_id
        post_case.call_and_validate()
    
    # Step 3: Always cleanup
    delete_case = data.draw(delete_user_strategy)
    delete_case.path_parameters = {"id": user_id}
    delete_case.call_and_validate()
```

This approach gives you complete control over the test sequence while benefiting from Schemathesis's schema-based data generation for each step.

### Integration with Other Frameworks

```python
from unittest import TestCase
from hypothesis import given

# Create strategies for specific operations
create_pet_strategy = schema["/pet"]["POST"].as_strategy()
get_pet_strategy = schema["/pet/{id}"]["GET"].as_strategy()

class TestAPI(TestCase):
    @given(case=create_pet_strategy)
    def test_create_pet(self, case):
        response = case.call_and_validate()
        self.assertIn("id", response.json())

    @given(create_case=create_pet_strategy, get_case=get_pet_strategy)
    def test_create_then_get(self, create_case, get_case):
        # Create pet
        create_response = create_case.call_and_validate()
        pet_id = create_response.json()["id"]

        # Get the same pet
        get_case.path_parameters = {"id": pet_id}
        get_response = get_case.call_and_validate()

        self.assertEqual(get_response.json()["id"], pet_id)
```

You can use Schemathesis strategies with regular `@given` decorators in any testing framework that supports Hypothesis.
