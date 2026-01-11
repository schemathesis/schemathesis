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
        # Generic prefixes - should use path context when it's a path parameter
        ("item_id", "/api/groups/{item_id}", "Group"),
        ("resource_id", "/api/users/{resource_id}", "User"),
        ("object_uuid", "/api/pets/{object_uuid}", "Pet"),
        ("entity-guid", "/api/orders/{entity-guid}", "Order"),
        # Bare slug - should use path context when it's a path parameter
        ("slug", "/api/recipes/{slug}", "Recipe"),
        # Non-generic prefixes - should still extract from parameter name
        ("user_id", "/api/members/{user_id}", "User"),
        ("recipe_slug", "/api/items/{recipe_slug}", "Recipe"),
        # Generic prefixes for query params - should NOT use path context (no placeholder in path)
        ("item_id", "/api/groups", "Item"),
        ("itemId", "/items/search", "Item"),
        # Name suffix - common for file/resource name parameters
        ("file_name", "/backups/{file_name}", "File"),
        ("recipe-name", "/recipes/{recipe-name}", "Recipe"),
        ("user_name", "/users/{user_name}", "User"),
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


@pytest.mark.parametrize(
    ["parameter", "resource", "fields", "expected"],
    [
        # Exact match
        pytest.param("id", "User", ["id", "name"], "id", id="exact-match"),
        pytest.param("name", "User", ["id", "name"], "name", id="exact-match-name"),
        # Normalized match
        pytest.param("userId", "User", ["user_id", "name"], "user_id", id="normalized-match"),
        # Parameter has resource prefix, field is suffix
        pytest.param("channelId", "Channel", ["id", "name"], "id", id="resource-prefix-in-param"),
        pytest.param("userId", "User", ["id", "email"], "id", id="user-id-to-id"),
        # ID synonym matching
        pytest.param("user_id", "User", ["uuid", "name"], "uuid", id="id-synonym-uuid"),
        pytest.param("item_id", "Item", ["guid", "name"], "guid", id="id-synonym-guid"),
        # Resource-hint matching (parameter prefix hints at resource, suffix is field)
        pytest.param("file_name", "BackupFile", ["name", "date", "size"], "name", id="resource-hint-file-name"),
        pytest.param("user_email", "User", ["id", "email", "name"], "email", id="resource-hint-user-email"),
        pytest.param("backup_file_name", "BackupFile", ["name"], "name", id="resource-hint-compound"),
        pytest.param("category_name", "RecipeCategory", ["id", "name", "slug"], "name", id="resource-hint-category"),
        # Resource-hint: resource starts with prefix (prefix matching)
        pytest.param("file_name", "FileManager", ["name"], "name", id="resource-hint-prefix-at-start"),
        pytest.param(
            "group_slug", "GroupSummary", ["id", "name", "slug"], "slug", id="resource-hint-prefix-group-slug"
        ),
        pytest.param(
            "household_slug", "HouseholdInDB", ["id", "name", "slug"], "slug", id="resource-hint-prefix-household"
        ),
        # Resource-hint: prefix too short (< 3 chars)
        pytest.param("xy_name", "Oxy", ["name"], None, id="resource-hint-prefix-too-short"),
        # No match cases
        pytest.param("random", "User", ["id", "name"], None, id="no-match"),
        pytest.param("the_name", "User", ["name"], None, id="no-match-resource-mismatch"),
    ],
)
def test_find_matching_field(parameter, resource, fields, expected):
    assert naming.find_matching_field(parameter=parameter, resource=resource, fields=fields) == expected


@pytest.mark.parametrize(
    ["name", "expected"],
    [
        # Hyphenated suffixes (safest - always normalize)
        pytest.param("Recipe-Output", "Recipe", id="hyphen-output"),
        pytest.param("Recipe-Input", "Recipe", id="hyphen-input"),
        pytest.param("User-Response", "User", id="hyphen-response"),
        pytest.param("Pet-Request", "Pet", id="hyphen-request"),
        pytest.param("IngredientFood-Output", "IngredientFood", id="hyphen-compound-name"),
        # PascalCase short suffixes (Out/In at word boundary)
        pytest.param("UserOut", "User", id="pascal-out"),
        pytest.param("UserIn", "User", id="pascal-in"),
        pytest.param("RecipeOut", "Recipe", id="pascal-out-recipe"),
        pytest.param("OrderIn", "Order", id="pascal-in-order"),
        # PascalCase Output/Input suffixes (at word boundary)
        pytest.param("RecipeOutput", "Recipe", id="pascal-output"),
        pytest.param("RecipeInput", "Recipe", id="pascal-input"),
        # PascalCase Response/Request are NOT normalized (often response wrappers)
        pytest.param("UserResponse", "UserResponse", id="pascal-response-not-normalized"),
        pytest.param("UserRequest", "UserRequest", id="pascal-request-not-normalized"),
        pytest.param("CountryResponse", "CountryResponse", id="country-response-wrapper"),
        # DTO patterns
        pytest.param("OrderDTO", "Order", id="dto-uppercase"),
        pytest.param("OrderDto", "Order", id="dto-titlecase"),
        pytest.param("UserDTO", "User", id="dto-user"),
        # Should NOT normalize - name is too short
        pytest.param("Output", "Output", id="just-output"),
        pytest.param("Input", "Input", id="just-input"),
        pytest.param("In", "In", id="just-in"),
        pytest.param("Out", "Out", id="just-out"),
        pytest.param("A-Output", "A-Output", id="single-char-base"),
        # Should NOT normalize - suffix is part of word (lowercase before suffix)
        pytest.param("Timeout", "Timeout", id="timeout-word"),
        pytest.param("Within", "Within", id="within-word"),
        pytest.param("Scout", "Scout", id="scout-word"),
        pytest.param("Spin", "Spin", id="spin-word"),
        pytest.param("Login", "Login", id="login-word"),
        pytest.param("Logout", "Logout", id="logout-word"),
        # Edge cases - PascalCase word boundary detection
        pytest.param("AudioOutput", "Audio", id="audio-output"),
        pytest.param("DataInput", "Data", id="data-input"),
        # Empty and short strings
        pytest.param("", "", id="empty"),
        pytest.param("X", "X", id="single-char"),
        pytest.param("AB", "AB", id="two-chars"),
    ],
)
def test_normalize_schema_name(name, expected):
    assert naming.normalize_schema_name(name) == expected
