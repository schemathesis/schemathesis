# Python API

Reference for Schemathesis public Python API.

## Schema Loaders

### Open API

::: schemathesis.openapi
    options:
      heading_level: 4
      show_root_toc_entry: false
      show_symbol_type_heading: true
      signature_crossrefs: true
      show_labels: true
      members:
      - from_url
      - from_path
      - from_file
      - from_asgi
      - from_wsgi
      - from_dict

### GraphQL

::: schemathesis.graphql
    options:
      heading_level: 4
      show_root_toc_entry: false
      show_symbol_type_heading: true
      signature_crossrefs: true
      show_labels: true
      members:
      - from_url
      - from_path
      - from_file
      - from_asgi
      - from_wsgi
      - from_dict


### Pytest

::: schemathesis.pytest
    options:
      heading_level: 4
      show_root_toc_entry: false
      show_symbol_type_heading: true
      signature_crossrefs: true
      show_labels: true
      members:
      - from_fixture

## Core Data Structures

::: schemathesis
    options:
      heading_level: 3
      show_root_toc_entry: false
      show_symbol_type_heading: true
      signature_crossrefs: true
      show_labels: true
      members:
      - Response
      - Case
      - APIOperation
      - BaseSchema

## Stateful Testing

::: schemathesis.stateful
    options:
      heading_level: 3
      show_root_toc_entry: false
      show_symbol_type_heading: true
      signature_crossrefs: true
      show_labels: true
      members:
      - APIStateMachine

## Hooks

::: schemathesis
    options:
      heading_level: 3
      show_root_toc_entry: false
      show_symbol_type_heading: true
      signature_crossrefs: true
      show_labels: true
      members:
      - HookContext
      - hook

## Checks

::: schemathesis
    options:
      heading_level: 3
      show_root_toc_entry: false
      show_symbol_type_heading: true
      signature_crossrefs: true
      show_labels: true
      members:
      - CheckContext
      - check

## Authentication

::: schemathesis
    options:
      heading_level: 3
      show_root_toc_entry: false
      show_symbol_type_heading: true
      signature_crossrefs: true
      show_labels: true
      members:
      - AuthContext
      - AuthProvider
      - auth

## Serialization

::: schemathesis
    options:
      heading_level: 3
      show_root_toc_entry: false
      show_symbol_type_heading: true
      signature_crossrefs: true
      show_labels: true
      members:
      - SerializationContext

### serializer

::: schemathesis.transport.SerializerRegistry.__call__
    options:
      heading_level: 4
      show_root_heading: false
      show_root_toc_entry: false
      show_symbol_type_heading: false
      signature_crossrefs: true
      show_labels: true

### serializer.alias

::: schemathesis.transport.SerializerRegistry.alias
    options:
      heading_level: 4
      show_root_heading: false
      show_root_toc_entry: false
      show_symbol_type_heading: false
      signature_crossrefs: true
      show_labels: true

## Response Deserialization

::: schemathesis
    options:
      heading_level: 3
      show_root_toc_entry: false
      show_symbol_type_heading: true
      signature_crossrefs: true
      show_labels: true
      members:
      - DeserializationContext
      - deserializer

## Targeted Property-based Testing

::: schemathesis
    options:
      heading_level: 3
      show_root_toc_entry: false
      show_symbol_type_heading: true
      signature_crossrefs: true
      show_labels: true
      members:
      - MetricContext
      - metric

## Open API Extensions

::: schemathesis.openapi
    options:
      heading_level: 3
      show_root_toc_entry: false
      show_symbol_type_heading: true
      signature_crossrefs: true
      show_labels: true
      members:
      - format
      - media_type

## GraphQL Extensions

::: schemathesis.graphql
    options:
      heading_level: 3
      show_root_toc_entry: false
      show_symbol_type_heading: true
      signature_crossrefs: true
      show_labels: true
      members:
      - scalar

### AST Node Factories

These factory functions from `schemathesis.graphql.nodes` convert Python values into GraphQL AST ValueNode objects for use with custom scalar strategies.

#### `String`

- `value` (str): String value to wrap

```python
from schemathesis.graphql import nodes
from hypothesis import strategies as st

# Email scalar using String node
st.emails().map(nodes.String)
```

#### `Int`

- `value` (int): Integer value to wrap

```python
# Positive integer scalar
st.integers(min_value=1).map(nodes.Int)
```

#### `Float`

- `value` (float): Float value to wrap

```python
# Price scalar with decimal precision
st.decimals(min_value=0, max_value=1000, places=2).map(nodes.Float)
```

#### `Boolean`

- `value` (bool): Boolean value to wrap

```python
# Active status scalar
st.booleans().map(nodes.Boolean)
```

#### `Enum`

- `value` (str): Enum value name to wrap

```python
# Status enum scalar
st.sampled_from(["ACTIVE", "INACTIVE", "PENDING"]).map(nodes.Enum)
```

#### `Null`

No arguments required. Represents GraphQL null values.

```python
# Optional scalar that can be null
st.one_of(st.emails().map(nodes.String), st.just(nodes.Null))
```

#### `List`

- `values` (list): List of ValueNode objects

```python
# List of integers
st.lists(st.integers().map(nodes.Int), min_size=1, max_size=5).map(nodes.List)
```

#### `Object`

- `fields` (list): List of ObjectFieldNode objects for object fields

```python
import graphql

# JSON object scalar (simplified)
def create_object_field(key, value):
    return graphql.ObjectFieldNode(
        name=graphql.NameNode(value=key),
        value=value
    )

# Simple object with string fields
st.dictionaries(
    keys=st.text(min_size=1, max_size=10),
    values=st.text().map(nodes.String),
    min_size=1, max_size=3
).map(lambda d: nodes.Object([
    create_object_field(k, v) for k, v in d.items()
]))
```
