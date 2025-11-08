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

Seen Schemathesis expose a bug? Open an issue or PR with the details.

Please include:

- Minimal reproduction (cURL command, failing test, or code snippet)
- What went wrong (status code, incorrect payload, side effect, etc.)
- Link to the upstream issue or fix, when available

Security vulnerabilities should follow responsible-disclosure rules; only document them here once the fix is public.

Maintainers curate the list to keep it clear and educational. Similar bugs may be consolidated.

**Submission template**

```
| Project | Type | Endpoint | Description | Status | Link |
|---------|------|----------|-------------|--------|------|
| YourProject | 💥 | `POST /endpoint` | What went wrong | Fixed | [#123](url) |
```

## Discoveries

Curated examples demonstrating edge cases Schemathesis can uncover:

| Project | Type | Endpoint | Description | Status | Link |
| --- | --- | --- | --- | --- | --- |
| *Coming soon* | | | Submit the first trophy! | | |
