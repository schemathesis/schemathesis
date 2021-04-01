from typing import Any, Dict, Optional

import requests
from starlette.testclient import TestClient as ASGIClient
from werkzeug import Client
from yarl import URL

from ...exceptions import HTTPError
from ...hooks import HookContext, dispatch
from ...utils import WSGIResponse, require_relative_url, setup_headers
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


def from_url(
    url: str, base_url: Optional[str] = None, port: Optional[int] = None, *, app: Any = None, **kwargs: Any
) -> GraphQLSchema:
    """Load GraphQL schema from the network.

    :param url: Schema URL.
    :param Optional[str] base_url: Base URL to send requests to.
    :param Optional[int] port: An optional port if you don't want to pass the ``base_url`` parameter, but only to change
                               port in ``url``.
    :param app: A WSGI app instance.
    :return: GraphQLSchema
    """
    setup_headers(kwargs)
    kwargs.setdefault("json", {"query": INTROSPECTION_QUERY})
    if not base_url and port:
        base_url = str(URL(url).with_port(port))
    response = requests.post(url, **kwargs)
    HTTPError.raise_for_status(response)
    decoded = response.json()
    return from_dict(raw_schema=decoded["data"], location=url, base_url=base_url, app=app)


def from_dict(
    raw_schema: Dict[str, Any], location: Optional[str] = None, base_url: Optional[str] = None, *, app: Any = None
) -> GraphQLSchema:
    """Load GraphQL schema from a Python dictionary.

    :param dict raw_schema: A schema to load.
    :param Optional[str] location: Optional schema location. Either a full URL or a filesystem path.
    :param Optional[str] base_url: Base URL to send requests to.
    :param app: A WSGI app instance.
    :return: GraphQLSchema
    """
    dispatch("before_load_schema", HookContext(), raw_schema)
    return GraphQLSchema(raw_schema, location=location, base_url=base_url, app=app)  # type: ignore


def from_wsgi(schema_path: str, app: Any, base_url: Optional[str] = None, **kwargs: Any) -> GraphQLSchema:
    """Load GraphQL schema from a WSGI app.

    :param str schema_path: An in-app relative URL to the schema.
    :param app: A WSGI app instance.
    :param Optional[str] base_url: Base URL to send requests to.
    :return: GraphQLSchema
    """
    require_relative_url(schema_path)
    setup_headers(kwargs)
    kwargs.setdefault("json", {"query": INTROSPECTION_QUERY})
    client = Client(app, WSGIResponse)
    response = client.post(schema_path, **kwargs)
    HTTPError.check_response(response, schema_path)
    return from_dict(raw_schema=response.json["data"], location=schema_path, base_url=base_url, app=app)


def from_asgi(
    schema_path: str,
    app: Any,
    base_url: Optional[str] = None,
    **kwargs: Any,
) -> GraphQLSchema:
    """Load GraphQL schema from an ASGI app.

    :param str schema_path: An in-app relative URL to the schema.
    :param app: An ASGI app instance.
    """
    require_relative_url(schema_path)
    setup_headers(kwargs)
    kwargs.setdefault("json", {"query": INTROSPECTION_QUERY})
    client = ASGIClient(app)
    response = client.post(schema_path, **kwargs)
    HTTPError.check_response(response, schema_path)
    return from_dict(
        response.json()["data"],
        location=schema_path,
        base_url=base_url,
        app=app,
    )
