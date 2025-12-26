import pytest

from schemathesis.specs.openapi.stateful.dependencies import naming


@pytest.mark.parametrize(
    ["parameter", "path", "expected"],
    [
        ("id", "/users/{id}", "User"),
        ("ID", "/accounts/{ID}", "Account"),
        ("userId", "/users/{userId}", "User"),
        ("accountUuid", "/accounts/{accountUuid}", "Account"),
        ("Uuid", "/accounts/{accountUuid}", None),
        ("sessionGuid", "/sessions/{sessionGuid}", "Session"),
        ("user_id", "/users/{user_id}", "User"),
        ("account_uuid", "/accounts/{account_uuid}", "Account"),
        ("_uuid", "/accounts/{account_uuid}", None),
        ("session-guid", "/sessions/{session-guid}", "Session"),
        ("messageSid", "/messages/{messageSid}", "Message"),
        ("Sid", "/messages/{messageSid}", None),
        ("group_slug", "/groups/{group_slug}", "Group"),
        ("household_slug", "/households/{household_slug}", "Household"),
        ("category-slug", "/categories/{category-slug}", "Category"),
        ("league_id_or_slug", "/leagues/{league_id_or_slug}", "League"),
        ("match-id-or-slug", "/matches/{match-id-or-slug}", "Match"),
        ("_slug", "/groups/{_slug}", None),
        ("id", "/users/{id}", "User"),
        ("_id", "/users/{_id}", None),
        ("uid", "/users/{uid}", None),
        ("someRandom", "/users/{someRandom}", None),
    ],
)
def test_from_parameter(parameter, path, expected):
    assert naming.from_parameter(parameter, path) == expected


@pytest.mark.parametrize(
    ["path", "expected"],
    [
        ("/api/groups/self", "Group"),
        ("/api/users/me", "User"),
        ("/api/accounts/current", "Account"),
        ("/api/groups", "Group"),
        ("/api/users/{user_id}", "User"),
        ("/self", "Self"),
        ("/api/users/ME", "User"),
        ("/api/groups/SELF", "Group"),
    ],
)
def test_from_path(path, expected):
    assert naming.from_path(path) == expected


@pytest.mark.parametrize(
    ["word", "expected"],
    [
        ("a", "a"),
        ("Chaos", "Chaos"),
        ("Expense", "Expense"),
        ("Focus", "Focus"),
        ("Licenses", "License"),
        ("Phases", "Phase"),
        ("Series", "Series"),
        ("Status", "Status"),
        ("aircraft", "aircraft"),
        ("axes", "axe"),
        ("beaches", "beach"),
        ("berries", "berry"),
        ("book", "book"),
        ("boxes", "box"),
        ("buses", "bus"),
        ("bushes", "bush"),
        ("buzzes", "buzz"),
        ("car", "car"),
        ("cars", "car"),
        ("cases", "case"),
        ("cats", "cat"),
        ("churches", "church"),
        ("cities", "city"),
        ("classes", "class"),
        ("crashes", "crash"),
        ("days", "day"),
        ("dice", "die"),
        ("dogs", "dog"),
        ("feet", "foot"),
        ("fizzes", "fizz"),
        ("flies", "fly"),
        ("foxes", "fox"),
        ("gases", "gas"),
        ("geese", "goose"),
        ("glasses", "glass"),
        ("houses", "house"),
        ("information", "information"),
        ("keys", "key"),
        ("masses", "mass"),
        ("oxen", "ox"),
        ("parties", "party"),
        ("party", "party"),
        ("passes", "pass"),
        ("process", "process"),
        ("reuses", "reuse"),
        ("sheep", "sheep"),
        ("software", "software"),
        ("statuses", "status"),
        ("teeth", "tooth"),
        ("uses", "use"),
        ("vases", "vase"),
        ("viruses", "virus"),
        ("watches", "watch"),
        ("wishes", "wish"),
    ],
)
def test_to_singular(word, expected):
    assert naming.to_singular(word) == expected


@pytest.mark.parametrize(
    ["word", "expected"],
    [
        ("Base", "Bases"),
        ("base", "bases"),
        ("basE", "bases"),
        ("beach", "beaches"),
        ("berry", "berries"),
        ("book", "books"),
        ("box", "boxes"),
        ("boy", "boys"),
        ("bus", "buses"),
        ("bush", "bushes"),
        ("buzz", "buzzes"),
        ("car", "cars"),
        ("church", "churches"),
        ("city", "cities"),
        ("class", "classes"),
        ("crash", "crashes"),
        ("day", "days"),
        ("die", "dice"),
        ("dog", "dogs"),
        ("echo", "echoes"),
        ("equipment", "equipment"),
        ("fizz", "fizzes"),
        ("fly", "flies"),
        ("focus", "focuses"),
        ("foot", "feet"),
        ("fox", "foxes"),
        ("gas", "gases"),
        ("glass", "glasses"),
        ("goose", "geese"),
        ("house", "houses"),
        ("information", "information"),
        ("key", "keys"),
        ("mass", "masses"),
        ("ox", "oxen"),
        ("party", "parties"),
        ("pass", "passes"),
        ("quiz", "quizzes"),
        ("ray", "rays"),
        ("sheep", "sheep"),
        ("status", "statuses"),
        ("table", "tables"),
        ("tax", "taxes"),
        ("tooth", "teeth"),
        ("use", "uses"),
        ("volcano", "volcanoes"),
        ("watch", "watches"),
        ("wish", "wishes"),
    ],
)
def test_to_plural(word, expected):
    assert naming.to_plural(word) == expected


@pytest.mark.parametrize(
    ["text", "expected"],
    [
        ("user_id", "UserId"),
        ("api_key", "ApiKey"),
        ("sip_trunk", "SipTrunk"),
        ("user-id", "UserId"),
        ("api-key", "ApiKey"),
        ("sipTrunk", "SipTrunk"),
        ("userId", "UserId"),
        ("accountHolder", "AccountHolder"),
        ("UserProfile", "UserProfile"),
        ("SipTrunk", "SipTrunk"),
        ("API", "API"),
        ("userID", "UserID"),
        ("user", "User"),
    ],
)
def test_to_pascal_case(text, expected):
    assert naming.to_pascal_case(text) == expected


@pytest.mark.parametrize(
    ["name", "prefixes", "suffixes", "expected"],
    [
        pytest.param("UserResponse", ["get"], ["response"], "User", id="suffix-only"),
        pytest.param("GetUser", ["get"], ["response"], "User", id="prefix-only"),
        pytest.param("GetUserResponse", ["get"], ["response"], "User", id="prefix-then-suffix-bug"),
        pytest.param("GETUserRESPONSE", ["get"], ["response"], "User", id="preserves-case"),
        pytest.param("User", ["get"], ["response"], "User", id="no-match"),
        pytest.param("CreateUser", ["get"], ["response"], "CreateUser", id="different-prefix"),
        pytest.param("", ["get"], ["response"], "", id="empty-string"),
        pytest.param("get", ["get"], [], "", id="becomes-empty"),
        pytest.param("GetUserResponse", [], [], "GetUserResponse", id="no-affixes"),
        pytest.param("GetUser", ["list", "get", "create"], ["response"], "User", id="first-prefix-wins"),
        pytest.param("UserResponse", ["get"], ["data", "response"], "User", id="first-suffix-wins"),
        pytest.param("  GetUser  ", ["get"], [], "User", id="strips-whitespace"),
        pytest.param("GetGetResponse", ["get"], ["response"], "Get", id="suffix-on-original"),
    ],
)
def test_strip_affixes(name, prefixes, suffixes, expected):
    assert naming.strip_affixes(name, prefixes, suffixes) == expected
