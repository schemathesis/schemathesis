from typing import Any, Dict, Optional

import requests
from yarl import URL

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


def from_url(url: str, base_url: Optional[str] = None, port: Optional[int] = None) -> GraphQLSchema:
    if not base_url and port:
        base_url = str(URL(url).with_port(port))
    response = requests.post(url, json={"query": INTROSPECTION_QUERY})
    decoded = response.json()
    return from_dict(raw_schema=decoded["data"], location=url, base_url=base_url)


def from_dict(
    raw_schema: Dict[str, Any], location: Optional[str] = None, base_url: Optional[str] = None
) -> GraphQLSchema:
    dispatch("before_load_schema", HookContext(), raw_schema)
    return GraphQLSchema(raw_schema, location=location, base_url=base_url)  # type: ignore
