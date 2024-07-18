import re

import pytest

import schemathesis
from schemathesis import filters
from schemathesis.exceptions import UsageError
from schemathesis.models import APIOperation

RAW_SCHEMA = {
    "openapi": "3.0.2",
    "info": {"title": "Test", "description": "Test", "version": "0.1.0"},
    "paths": {
        "/users/": {
            "get": {
                "responses": {"200": {"description": "OK"}},
                "tags": ["Users"],
                "operationId": "getUsers",
            },
            "post": {"deprecated": True, "responses": {"200": {"description": "OK"}}, "tags": ["Users"]},
        },
        "/users/{user_id}/": {
            "patch": {
                "operationId": "patchUser",
                "responses": {"200": {"description": "OK"}},
            },
        },
        "/orders/": {
            "get": {"responses": {"200": {"description": "OK"}}, "tags": ["Orders", "SomeOther"]},
            "post": {"responses": {"200": {"description": "OK"}}, "tags": []},
        },
    },
}
SCHEMA = schemathesis.from_dict(RAW_SCHEMA)
USERS_GET = SCHEMA["/users/"]["GET"]
USERS_POST = SCHEMA["/users/"]["POST"]
USER_ID_PATCH = SCHEMA["/users/{user_id}/"]["PATCH"]
ORDERS_GET = SCHEMA["/orders/"]["GET"]
ORDERS_POST = SCHEMA["/orders/"]["POST"]
OPERATIONS = [USERS_GET, USERS_POST, USER_ID_PATCH, ORDERS_GET, ORDERS_POST]
NO_PATCH = [USERS_GET, USERS_POST, ORDERS_GET, ORDERS_POST]
SINGLE_INCLUDE_CASES = (
    ({"path": "/users/"}, [USERS_GET, USERS_POST]),
    ({"path": ["/users/", "/orders/"]}, NO_PATCH),
    ({"path_regex": "^/users/"}, [USERS_GET, USERS_POST, USER_ID_PATCH]),
    ({"tag": "Users"}, [USERS_GET, USERS_POST]),
    ({"tag": ["Users", "Orders"]}, [USERS_GET, USERS_POST, ORDERS_GET]),
    ({"tag_regex": ".+rs"}, [USERS_GET, USERS_POST, ORDERS_GET]),
    ({"method": "GET"}, [USERS_GET, ORDERS_GET]),
    ({"method": ["GET", "PATCH"]}, [USERS_GET, USER_ID_PATCH, ORDERS_GET]),
    ({"method_regex": "^P"}, [USERS_POST, USER_ID_PATCH, ORDERS_POST]),
    ({"name": "GET /users/"}, [USERS_GET]),
    ({"name": ["GET /users/", "POST /orders/"]}, [USERS_GET, ORDERS_POST]),
    ({"name_regex": "^P.+ /(users|orders)/"}, [USERS_POST, USER_ID_PATCH, ORDERS_POST]),
    ({"name_regex": re.compile("^p.+ /(USERS|orders)/", re.IGNORECASE)}, [USERS_POST, USER_ID_PATCH, ORDERS_POST]),
    ({"operation_id": "getUsers"}, [USERS_GET]),
    ({"operation_id": ["getUsers", "patchUser"]}, [USERS_GET, USER_ID_PATCH]),
    ({"operation_id_regex": ".+Use.+"}, [USERS_GET, USER_ID_PATCH]),
)
MULTI_INCLUDE_CASES = [
    (({"path": "/users/"}, {"path": "/orders/"}), NO_PATCH),
    (({"path": ["/users/"]}, {"path": ["/orders/"]}), NO_PATCH),
    (({"path_regex": "^/users/$"}, {"path_regex": "^/orders/"}), NO_PATCH),
    (({"method": "POST"}, {"method": "GET"}), NO_PATCH),
    (({"method": ["POST"]}, {"method": ["GET"]}), NO_PATCH),
    (({"method_regex": "^P.+T$"}, {"method_regex": "^G"}), NO_PATCH),
    (({"name": "GET /users/"}, {"name": "GET /orders/"}), [USERS_GET, ORDERS_GET]),
    (({"name": ["GET /users/", "POST /users/"]}, {"name": ["GET /orders/", "POST /orders/"]}), NO_PATCH),
    (({"name_regex": "^P.+T /users/$"}, {"name_regex": "^G.+ /orders/$"}), [USERS_POST, ORDERS_GET]),
    (
        ({"path": "/users/", "method_regex": "GET|POST"}, {"name_regex": "^G.+ /orders/$"}),
        [USERS_GET, USERS_POST, ORDERS_GET],
    ),
]


def case_id(case):
    if isinstance(case[0], APIOperation):
        return "expected"

    def fmt_item(key, value):
        if isinstance(value, list):
            return f"{key}_list"
        return key

    def fmt_kwargs(kwargs):
        return ",".join(fmt_item(key, value) for key, value in kwargs.items())

    return "-".join(f"{kind}-{fmt_kwargs(kwargs)}" for kind, kwargs in case)


@pytest.mark.parametrize(
    "chain, expected",
    [
        (
            [("include", kwargs)],
            selection,
        )
        for (kwargs, selection) in SINGLE_INCLUDE_CASES
    ]
    + [
        (
            [("exclude", kwargs)],
            [o for o in OPERATIONS if o not in selection],
        )
        for (kwargs, selection) in SINGLE_INCLUDE_CASES
    ]
    + [
        (
            [("include", kwargs) for kwargs in chain],
            selection,
        )
        for (chain, selection) in MULTI_INCLUDE_CASES
    ]
    + [
        (
            [("exclude", kwargs) for kwargs in chain],
            [o for o in OPERATIONS if o not in selection],
        )
        for (chain, selection) in MULTI_INCLUDE_CASES
    ]
    + [
        ([("include", {"path_regex": "^/u"}), ("exclude", {"method_regex": "..TCH|DELET"})], [USERS_GET, USERS_POST]),
        (
            [("include", {"name": "GET /orders/"}), ("exclude", {"method": "POST"}), ("exclude", {"path": "/users/"})],
            [ORDERS_GET],
        ),
        ([("include", {"method": "GET"}), ("exclude", {"path_regex": "^/users/"})], [ORDERS_GET]),
        (
            [
                ("include", {"path": "/users/"}),
                ("exclude", {"func": lambda ctx: ctx.operation.definition.raw.get("deprecated") is True}),
            ],
            [USERS_GET],
        ),
    ],
    ids=case_id,
)
def test_matchers(chain, expected):
    filter_set = filters.FilterSet()
    schema = SCHEMA
    for method, kwargs in chain:
        getattr(filter_set, method)(**kwargs)
        schema = getattr(schema, method)(**kwargs)
    assert filter_set.apply_to(OPERATIONS) == expected
    assert schema.filter_set.apply_to(OPERATIONS) == expected


def matcher_func(ctx):
    return True


@pytest.mark.parametrize(
    "matchers, expected",
    (
        ([filters.Matcher.for_function(matcher_func)], "<Filter: [matcher_func]>"),
        ([filters.Matcher.for_function(lambda ctx: True)], "<Filter: [<lambda>]>"),
        ([filters.Matcher.for_value("method", "POST")], "<Filter: [method='POST']>"),
        (
            [filters.Matcher.for_value("method", "POST"), filters.Matcher.for_value("path", "/users/")],
            "<Filter: [method='POST' && path='/users/']>",
        ),
        ([filters.Matcher.for_regex("path", "^/u")], "<Filter: [path_regex=re.compile('^/u')]>"),
        (
            [filters.Matcher.for_regex("path", re.compile("^/u", re.IGNORECASE))],
            "<Filter: [path_regex=re.compile('^/u', re.IGNORECASE)]>",
        ),
    ),
)
def test_filter_repr(matchers, expected):
    assert repr(filters.Filter(matchers)) == expected


def test_matcher_repr():
    assert repr(filters.Matcher.for_value("method", "POST")) == "<Matcher: method='POST'>"


@pytest.mark.parametrize(
    "args, kwargs, expected",
    (
        (
            (matcher_func,),
            {},
            "[<Filter: [matcher_func]>]",
        ),
        (
            (matcher_func,),
            {"deprecated": True},
            "[<Filter: [is_deprecated]>, <Filter: [matcher_func]>]",
        ),
        (
            (),
            {"deprecated": True},
            "[<Filter: [is_deprecated]>]",
        ),
    ),
)
def test_exclude_custom(args, kwargs, expected):
    lazy_schema = schemathesis.from_pytest_fixture("name")
    schemas = [SCHEMA, lazy_schema]
    for schema in schemas:
        assert (
            repr(sorted(schema.exclude(*args, **kwargs).filter_set._excludes, key=lambda x: x.matchers[0].label))
            == expected
        )


def test_sanity_checks():
    with pytest.raises(UsageError, match=filters.ERROR_EMPTY_FILTER):
        filters.FilterSet().include()


def test_attach_filter_chain():
    def auth():
        pass

    filter_set = filters.FilterSet()
    filters.attach_filter_chain(auth, "apply_to", filter_set.include)
    # Returns the same object
    assert auth.apply_to(method="GET", path="/users/") is auth
    assert not filter_set.is_empty()
    assert len(filter_set._includes) == 1
    assert repr(list(filter_set._includes)[0]) == "<Filter: [method='GET' && path='/users/']>"


@pytest.mark.parametrize("method", (filters.FilterSet.include, filters.FilterSet.exclude))
@pytest.mark.parametrize(
    "kwargs",
    (
        {"name": "foo"},
        {"func": matcher_func},
        {"func": matcher_func, "method": "POST"},
        {"func": lambda o: True},
    ),
)
def test_repeating_filter(method, kwargs):
    # Adding the same filter twice is an error
    filter_set = filters.FilterSet()
    filter_set.include(**kwargs)
    with pytest.raises(UsageError, match=filters.ERROR_FILTER_EXISTS):
        method(filter_set, **kwargs)


def test_forbid_value_and_auth():
    filter_set = filters.FilterSet()
    with pytest.raises(UsageError, match=filters.ERROR_EXPECTED_AND_REGEX):
        filter_set.include(method="POST", method_regex="GET")
