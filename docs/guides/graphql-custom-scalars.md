# GraphQL Custom Scalars

Configure Schemathesis to generate appropriate test data for custom scalar types in your GraphQL schema.

## Built-in support

Schemathesis automatically handles these common custom scalars without any configuration:

- `Date`, `Time`, `DateTime` - ISO formatted date/time strings
- `UUID` - Valid UUID strings  
- `IP`, `IPv4`, `IPv6` - Valid IP addresses
- `BigInt`, `Long` - Large integers

```graphql
# Your schema:
scalar Date
scalar UUID

type Query {
  getEvents(date: Date!, id: UUID!): [Event]
}
```

Schemathesis automatically generates valid queries:
```graphql
{ getEvents(date: "2023-12-25", id: "550e8400-e29b-41d4-a716-446655440000") }
```

## Adding custom scalars

For scalars not covered by built-in support, register custom strategies before loading your schema:

```python
import schemathesis
from hypothesis import strategies as st
from schemathesis.graphql import nodes

# Configure custom scalars
schemathesis.graphql.scalar("Email", st.emails().map(nodes.String))
schemathesis.graphql.scalar(
    "PositiveInt", st.integers(min_value=1).map(nodes.Int)
)

# Load schema and run tests
schema = schemathesis.graphql.from_url("http://localhost:8000/graphql")

@schema.parametrize()
def test_graphql_api(case):
    case.call_and_validate()
```

!!! info "Hypothesis strategies reference"
    For all available data generation strategies, see [the Hypothesis strategies documentation](https://hypothesis.readthedocs.io/en/latest/reference/strategies.html).

## Common scalar examples

**String-based scalars:**
```python
schemathesis.graphql.scalar("Email", st.emails().map(nodes.String))
schemathesis.graphql.scalar("URL", st.urls().map(nodes.String))
schemathesis.graphql.scalar(
    "Phone", st.from_regex(r"\+1-\d{3}-\d{3}-\d{4}").map(nodes.String)
)
```

**Numeric scalars:**
```python
schemathesis.graphql.scalar("Percentage", st.integers(0, 100).map(nodes.Int))
schemathesis.graphql.scalar(
    "Price", st.decimals(min_value=0, max_value=1000, places=2).map(nodes.Float)
)
```

**Constrained scalars:**
```python
# Restricted date range
from datetime import date

schemathesis.graphql.scalar(
    "RecentDate", 
    st.dates(
        min_value=date(2020, 1, 1), 
        max_value=date(2030, 12, 31)
    ).map(str).map(nodes.String)
)
```

## Available AST node types

Use these `schemathesis.graphql.nodes` factories to wrap your generated values:

- `String(value)`, `Int(value)`, `Float(value)`, `Boolean(value)`, `Enum(value)`, `Null`
- `List(values)`, `Object(fields)` - For complex types (see advanced section)

## Advanced: JSON scalars

For JSON scalars that accept arbitrary objects, you need to convert Python dictionaries to GraphQL AST nodes:

```python
import graphql
from hypothesis import strategies as st

import schemathesis
from schemathesis.graphql import nodes

def dict_to_object_fields(data: dict) -> list:
    """Convert a dictionary to a list of ObjectFieldNode instances."""
    fields = []
    for key, value in data.items():
        name_node = graphql.NameNode(value=key)
        value_node = python_value_to_ast_node(value)
        field_node = graphql.ObjectFieldNode(name=name_node, value=value_node)
        fields.append(field_node)
    return fields

def python_value_to_ast_node(value):
    """Convert a Python value to the appropriate GraphQL AST ValueNode."""
    if value is None:
        return graphql.NullValueNode()
    elif isinstance(value, bool):
        return graphql.BooleanValueNode(value=value)
    elif isinstance(value, int):
        return graphql.IntValueNode(value=str(value))
    elif isinstance(value, float):
        return graphql.FloatValueNode(value=str(value))
    elif isinstance(value, str):
        return graphql.StringValueNode(value=value)
    elif isinstance(value, list):
        ast_values = [python_value_to_ast_node(item) for item in value]
        return graphql.ListValueNode(values=tuple(ast_values))
    elif isinstance(value, dict):
        fields = dict_to_object_fields(value)
        return graphql.ObjectValueNode(fields=tuple(fields))
    raise ValueError("Unsupported value")

# Register JSON scalar
alphabet = st.characters(min_codepoint=ord("A"), max_codepoint=ord("Z"))
schemathesis.graphql.scalar(
    "JSON",
    st.dictionaries(
        keys=st.text(min_size=1, max_size=10, alphabet=alphabet),
        values=st.recursive(
            st.text(alphabet=alphabet)
            | st.integers()
            | st.floats(allow_nan=False, allow_infinity=False)
            | st.booleans()
            | st.none(),
            lambda strategy: st.lists(strategy, max_size=3)
            | st.dictionaries(
                  keys=st.text(min_size=1, max_size=10, alphabet=alphabet), 
                  values=strategy, 
                  max_size=3
              ),
        ),
        min_size=1,
        max_size=5,
    ).map(lambda d: nodes.Object(dict_to_object_fields(d))),
)
```
