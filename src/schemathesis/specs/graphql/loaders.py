from typing import Any, Dict, Optional

import requests

from ...hooks import HookContext, dispatch
from .schemas import GraphQLSchema

INTROSPECTION_QUERY = """
query IntrospectionQuery {
  __schema {
    queryType { name }
    mutationType { name }
    subscriptionType { name }
    types {
      ...FullType
    }
    directives {
      name
      locations
      args {
        ...InputValue
      }
    }
  }
}
fragment FullType on __Type {
  kind
  name
  fields(includeDeprecated: true) {
    name
    args {
      ...InputValue
    }
    type {
      ...TypeRef
    }
    isDeprecated
    deprecationReason
  }
  inputFields {
    ...InputValue
  }
  interfaces {
    ...TypeRef
  }
  enumValues(includeDeprecated: true) {
    name
    isDeprecated
    deprecationReason
  }
  possibleTypes {
    ...TypeRef
  }
}
fragment InputValue on __InputValue {
  name
  type { ...TypeRef }
  defaultValue
}
fragment TypeRef on __Type {
  kind
  name
  ofType {
    kind
    name
    ofType {
      kind
      name
      ofType {
        kind
        name
        ofType {
          kind
          name
          ofType {
            kind
            name
            ofType {
              kind
              name
              ofType {
                kind
                name
              }
            }
          }
        }
      }
    }
  }
}"""


def from_url(url: str) -> GraphQLSchema:
    response = requests.post(url, json={"query": INTROSPECTION_QUERY})
    decoded = response.json()
    return from_dict(raw_schema=decoded["data"], location=url)


def from_dict(raw_schema: Dict[str, Any], location: Optional[str] = None) -> GraphQLSchema:
    dispatch("before_load_schema", HookContext(), raw_schema)
    return GraphQLSchema(raw_schema, location=location)
