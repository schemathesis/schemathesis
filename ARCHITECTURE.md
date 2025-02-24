# Architecture

This document outlines the internal structure of Schemathesis for developers working on the core codebase.

## Overview

- **Core**: Foundation layer with no external dependencies
- **Intermediate**: Hypothesis-powered primitives
- **Internal subsystems**: Engine, OpenAPI, and GraphQL
- **Public API**: Curated interface combining functionality from all layers

## Core Layer

Independent utilities that form the foundation of Schemathesis without external dependencies.

### `core/marks`

Attaches Schemathesis-specific metadata to test functions, enabling integration with external testing frameworks (Hypothesis, pytest).

### `core/loaders`

Schema loading functionality, supporting multiple sources:

- File-based schemas
- HTTP-based schema retrieval

### `core/transports`

Network communication primitives for test execution

- Unified response structure

### `core/output`

Output processing utilities:

- Response formatting
- Data sanitization

## Intermediate Layer

Building blocks for test generation and execution:

### `generation/hypothesis`

Core test generation machinery:

- Hypothesis test creation
- Example generation
- Test execution control

### `generation/case`

Defines the `Case` class - the fundamental container for test data that flows through the system.

### `generation/stateful`

State machine implementations for API sequence testing.

### `transport`

Higher-level transport implementations building on core primitives.

## Internal Subsystems

### Engine

- Test execution orchestration
- Event system for tracking test progress
- Testing phases management

### API Specifications

- OpenAPI implementation
- GraphQL implementation

## Public API

Python API for end users:

- Re-exports from lower layers
- Common extension points
- Entry points for running tests
