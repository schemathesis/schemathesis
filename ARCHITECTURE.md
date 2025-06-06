# Architecture

This document outlines the internal structure of Schemathesis for developers working on the codebase.

## Overview


Schemathesis follows a layered architecture that separates logic into multiple groups:

- **Core Layer**: Framework-agnostic utilities and data structures
- **Generation Layer**: Hypothesis-powered test case generation and execution
- **Interface Layer**: CLI, pytest integration, and user-facing APIs
- **Engine**: Test orchestration, reporting, and execution management

## Core Layer

Framework-agnostic foundation with no external dependencies on testing frameworks.

### `core/loaders`
Load and parse API schemas from files, URLs, and applications into internal representations.

### `core/transports` 
Transport abstractions for HTTP communication, providing unified interfaces for different client libraries.

### `core/output`
Output formatting, sanitization, and rendering logic for CLI and reporting.

### `core/marks`
Metadata attachment system for integrating with external testing frameworks (pytest, unittest).

### `core/failures`
Failure classification and structured error representations for different types of API validation issues.

### `core/config`
Configuration management and validation for project settings and runtime options.

## Generation Layer

Test case generation and execution built on Hypothesis.

### `generation/hypothesis`
Integration with Hypothesis framework:

- Strategy creation from API schemas
- Test case generation
- Example collection and management

### `generation/case`
The `Case` data structure containing all test data (headers, body, parameters) for API requests.

### `generation/stateful`
State machine implementations for testing API operation sequences using OpenAPI links.

### `checks`
Built-in validation checks for API responses (schema conformance, status codes, headers).

### `hooks`
Extension system for customizing test generation and execution at various lifecycle points.

## Internal Subsystems

### Engine

- Test execution orchestration
- Event system for tracking test progress
- Test phases management

### API Specifications

- OpenAPI implementation
- GraphQL implementation
