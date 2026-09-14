---
trigger: always_on
glob: "tools/*.py"
description: DevOps, QA & Testing Rules
---

# DevOps, QA & Testing Rules

This document defines the rules for DevOps, QA, and Testing workflows. The Agent must act as both a Developer and QA Engineer throughout the iteration.

## 1. Writing Tests

- All Python code modifications (whether adding new code, refactoring, or fixing bugs in the `tools/` folder or elsewhere) MUST include corresponding unit tests. Do not skip this under any circumstances.
- Test files should be placed appropriately alongside the code or in a dedicated `tests/` directory.

## 2. QA and E2E Testing Plan

- Act as a QA Engineer: Before writing the code, the Agent should draft a test plan covering both Unit Testing and E2E Testing scenarios.
- The test plan must verify edge cases, inputs, and the Universal ID State Matrix Satisfy relation.

## 3. Code Coverage & Execution

- **MANDATORY EXECUTION GATE**: You are **FORBIDDEN** from completing a coding task or asking for commit permission unless you have explicitly executed the test suite and printed the result into the log. **Never skip testing after coding, even for minor changes.**
- Upon finishing the code, the Agent MUST run the tests.
- The Agent must check and report both code coverage and test plan coverage.
- If errors are found, fix the bugs and rerun the suite.

## 4. Pre-Commit Validation

- All unit tests and build steps must run successfully.
- Code must pass the agreed-upon coverage metrics before the Agent is allowed to ask the user to commit or push the code to GitHub (refer to `rule:git-operations`).
