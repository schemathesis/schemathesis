# Schemathesis Trophy Case

Real-world defects uncovered by Schemathesis’ property-based testing engine.

## Bug Categories

| Type | Description |
| --- | --- |
| 💥 Server Crashes | 5xx responses or crashes triggered by unexpected inputs |
| 📋 Schema Violations | Responses that violate the published contract |
| 🚪 Validation Bypass | Invalid or malicious data accepted by the API |
| 🔗 Integration Issues | Incompatibilities between clients and servers |

## Submitting a Trophy

<div align="center" markdown>

[Submit a Trophy :fontawesome-solid-trophy:](https://github.com/schemathesis/schemathesis/issues/new?template=trophy-submission.yml){ .md-button .md-button--primary }

</div>

!!! info "What we're looking for"
    Bugs in APIs that other developers use or recognize (open-source projects with active communities, public SaaS APIs, popular tools).

    Security vulnerabilities should follow responsible-disclosure rules; only document them here once the fix is public.

## Discoveries

| Project | Type | What Schemathesis found |
| --- | --- | --- |
| [Huma](https://github.com/danielgtaylor/huma/issues/1042) | 💥 Server Crashes | `uniqueItems` validation ran before type casting, crashing the server thread on certain primitive inputs. |
