# Fuzz Dictionaries

Feed any values you want Schemathesis to try: SQL injection payloads, real IDs, edge-case strings, anything. Mix them with schema-derived data at a chosen probability, or force them for a specific parameter.

Entries are plain values in TOML or a text file, so a list exported from your database or produced by an LLM drops straight in - no code, no plugin.

Dictionary entries are filtered by the active generation mode: positive mode samples only schema-valid entries, negative mode samples only schema-violating ones. If a binding has no eligible entries for the active mode, that parameter falls back to normal schema-derived generation.

Dictionaries apply in the **fuzzing** and **stateful** phases. The examples and coverage phases send their own values and ignore dictionary bindings; to force one value in every phase, use a [parameter override](../reference/configuration.md#parameter-overrides) (`"path.user_id" = 42`).

## Probe a Wordlist Against Every String Parameter

Save a libFuzzer/AFL-style dictionary file next to your config:

```text
# fuzz/sql_injection.dict
"' OR 1=1--"
"1'\x20OR\x20'1'='1"
"admin'--"
"'; DROP TABLE users--"
```

Wire it into `schemathesis.toml`:

```toml
[dictionaries.sql]
from-file = "fuzz/sql_injection.dict"

[generation.dictionaries]
string = { dictionary = "sql", probability = 0.1 }
```

Schemathesis draws from `fuzz/sql_injection.dict` for roughly 10% of every string parameter (query, path, header, cookie) and uses normal schema-derived values for the rest. `from-file` paths resolve relative to the config file directory; absolute paths work too.

## Force a Known-Real Value Into a Path Parameter

When `GET /users/{user_id}` keeps returning 404, give the fuzzer real IDs:

```toml
[dictionaries.users]
values = [42, 100, 9999]

[parameters]
"path.user_id" = { dictionary = "users" }
```

In the fuzzing and stateful phases, positive-mode requests to `/users/{user_id}` use one of `42`, `100`, `9999`. The default probability for a parameter-specific binding is `1.0` (the dictionary is always used). Negative-mode cases need schema-violating entries; these three are valid integers, so negative cases generate `user_id` as usual.

Lower the probability to mix dictionary draws with random IDs:

```toml
[parameters]
"path.user_id" = { dictionary = "users", probability = 0.5 }
```

## Target Body Fields

For request bodies, address fields with a JSONPath subset rooted at `body.`:

```toml
[dictionaries.card_numbers]
values = ["4111-1111-1111-1111", "5500-0000-0000-0004"]

[dictionaries.emails]
values = ["test@example.com", "admin@example.com"]

[parameters]
"body.ccNumber" = { dictionary = "card_numbers" }
"body.user.email" = { dictionary = "emails" }
```

Use `[*]` to substitute at every array element:

```toml
[dictionaries.tag_words]
values = ["sale", "featured", "archived"]

[parameters]
"body.tags[*]" = { dictionary = "tag_words", probability = 0.4 }
"body.items[*].name" = { dictionary = "tag_words" }
```

For a body that's itself a top-level array, use `body.[*]`. Recursive descent (`body..x`), positional indices (`body.x[3]`), filters, and slices are not supported.

## Target GraphQL Arguments

Address an argument by the type that declares its field:

```toml
[dictionaries.titles]
values = ["Dune", "Solaris"]

[parameters]
"Query.bookByTitle.title" = { dictionary = "titles" }
```

Use `*` for any type or any field - `*.*.title` covers every `title` argument in the schema, `Query.*.title` every one on `Query`:

```toml
[parameters]
"*.*.title" = { dictionary = "titles" }
```

The same key addresses a nested field's arguments, since `<Type>` is the type that declares the field:

```toml
[dictionaries.genres]
values = ["fantasy", "science fiction"]

[parameters]
"Author.books.genre" = { dictionary = "genres" }
```

Descend into input objects with a dotted suffix, and target list elements with `[*]` - the same vocabulary as `body.` paths:

```toml
[dictionaries.currencies]
values = ["USD", "EUR", "XXX"]

[dictionaries.tag_words]
values = ["sale", "featured", "archived"]

[parameters]
"Mutation.createOrder.input.currency" = { dictionary = "currencies" }
"Query.search.tags[*]" = { dictionary = "tag_words" }
```

Type-wide bindings (`[generation.dictionaries] string = ...`) apply to GraphQL arguments too.

Limits: only `String`, `ID`, `Int` and `Float` arguments are filled - enums, `Boolean` and custom scalars are left alone. Substitution happens in positive mode only, and only for arguments the generated query already carries, so a binding on an omitted optional argument is a no-op.

## Apply Different Dictionaries Per Operation

Use `[[operations]]` with the existing filter vocabulary:

```toml
[dictionaries.sql]
from-file = "fuzz/sql_injection.dict"

[dictionaries.search_terms]
values = ["a", "*", "%20", "?"]

[[operations]]
include-tag = "search"
generation.dictionaries.string = { dictionary = "search_terms", probability = 0.3 }

[[operations]]
include-path-regex = "/admin/"
generation.dictionaries.string = { dictionary = "sql", probability = 0.2 }
```

Operation-scoped bindings override global ones for matching operations.

## Inline Values vs File Dictionaries

Use inline `values` for small, version-controlled lists:

```toml
[dictionaries.http_methods]
values = ["GET", "POST", "PUT", "DELETE", "TRACE"]

[dictionaries.boundaries]
values = [-1, 0, 1, 2147483647, 2147483648]
```

Use `from-file` for existing fuzz dictionaries (libFuzzer, AFL, or any hand-rolled wordlist):

```text
# Quoted entries, one per line:
"admin"
"root"

# Named entries (the name is decorative):
crlf="\r\n"
nul="\x00"

# Comments and blank lines are ignored.
```

Supported escapes: `\\`, `\"`, `\n`, `\r`, `\t`, `\xAB`. Parse errors report the file and line number.

## Type Eligibility

A dictionary's entries are filtered against the target JSON Schema type:

- `string`: every entry is eligible; values are coerced with `str()` if needed.
- `integer`: only entries that parse as base-10 integers (`"42"` or `42`, not `"4.2"` or `"abc"`).
- `number`: only entries that parse as finite numbers (integer, decimal, or exponent forms).

A type-wide binding whose dictionary has zero eligible entries for the target type is a config error at load time, not a silent no-op.

## Precedence

When multiple bindings could apply to the same parameter, the highest-priority one wins:

1. Operation-specific parameter binding (`[[operations]] parameters."query.q" = { dictionary = "..." }`)
2. Global parameter binding (`[parameters] "query.q" = { dictionary = "..." }`)
3. Operation-specific type-wide binding (`[[operations]] generation.dictionaries.string = ...`)
4. Global type-wide binding (`[generation.dictionaries] string = ...`)
5. Normal schema-derived generation

For GraphQL, a binding naming both the type and the field beats one with `*` in either position, at the same scope.

Dictionary entries overwrite values the runtime resource pool or source-extracted constants put in the same slot; stateful transitions still set their own linked values last.

Scalar parameter overrides (`parameters.api_version = "v2"`) still force the exact value and override any dictionary binding.

## Troubleshooting

**`... references unknown dictionary`.** Every `dictionary = "..."` binding needs a matching `[dictionaries.<name>]` table in the same config.

**`Dictionary file ... not found`.** `from-file` paths resolve relative to the directory of `schemathesis.toml`, not the current working directory.

**Entries never show up in requests.** Check the phase and mode: dictionaries apply only in the fuzzing and stateful phases, and only entries valid for the active mode are sampled. For GraphQL, the argument must be present in the generated query.
