# Architecture

This document outlines the internal structure of Schemathesis for developers working on the codebase.

## Overview

Schemathesis aims to separate concerns and isolates logic into multiple layers:

- **Core**: Basic utilities and data structures with no external dependencies
- **Intermediate**: Hypothesis-powered primitives for data generation
- **Engine**: A test runner to orchestrate test execution and failure reporting

## Core Layer

### `core/marks`

Attaches Schemathesis-specific metadata to test functions, enabling integration with external testing frameworks (Hypothesis, pytest).

### `core/loaders`

Load API schemas from various sources and transform them into an internal representation which is used throughout the codebase.

### `core/transports`

Set of primitives for communicating with the tested API, including unified `Response` structure.

### `core/output`

Logic for formatting and sanitizing Schemathesis output.

## Intermediate Layer

Building blocks for test generation and execution:

### `generation/hypothesis`

Core test generation machinery:

- Hypothesis test creation
- Collecting examples from API schemas

### `generation/case`

Defines the `Case` data structure - the fundamental container for generated data which is serialized and sent to the tested API.

### `generation/stateful`

State machine implementations for stateful API testing.

### `transport`

Higher-level transport implementations building on core primitives - it connects external transport libraries (like `requests`) with the rest of Schemathesis codebase.

## Internal Subsystems

### Engine

- Test execution orchestration
- Event system for tracking test progress
- Test phases management

### API Specifications

- OpenAPI implementation
- GraphQL implementation

