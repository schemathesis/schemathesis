# Architecture

This document provides a high-level overview of Schemathesis for developers working on the codebase.

## Key Concepts

**Test Phases** - Schemathesis executes tests in phases:

- **Examples**: Uses examples from the schema
- **Coverage**: Systematically generates cases for schema constraints
- **Fuzzing**: Random generation via Hypothesis
- **Stateful**: Multi-step sequences using API links

**Generation Modes** - Test cases are generated as:

- **Positive**: Valid data conforming to the schema
- **Negative**: Invalid data violating schema constraints

Checks run independently on all generated cases regardless of mode.

## Layers

| Layer | Purpose |
|-------|---------|
| **Core** | Framework-agnostic utilities: transport, config, error handling |
| **Specs** | Parses OpenAPI/GraphQL schemas into `APIOperation` instances |
| **Generation** | Creates `Case` instances from operations using Hypothesis |
| **Engine** | Orchestrates test execution across phases, runs checks |
| **Interface** | User-facing CLI and pytest plugin |

## Directory Structure

```
src/schemathesis/
├── core/           # Core utilities
├── specs/          # OpenAPI and GraphQL implementations
├── generation/     # Test case generation
├── engine/         # Test orchestration and phases
├── cli/            # Command-line interface
├── pytest/         # pytest plugin
├── checks.py       # Validation checks
├── hooks.py        # Extension points
└── schemas.py      # Base schema classes
```

## Data Flow

```
Schema (file/URL)
    ↓
Specs Layer (parse)
    ↓
APIOperation
    ↓
Generation Layer (create test data)
    ↓
Case
    ↓
Engine (send request, run checks)
    ↓
Results → Interface Layer (report)
```
