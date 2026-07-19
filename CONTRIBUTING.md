# Contributing

Thank you for your interest in contributing! This document covers everything you need to get started.

## Table of Contents

- [Getting Started](#getting-started)
  - [Prerequisites](#prerequisites)
  - [Development Setup](#development-setup)
- [Making Changes](#making-changes)
  - [Branching Strategy](#branching-strategy)
  - [Code Style](#code-style)
  - [Commit Messages](#commit-messages)
  - [Running Tests](#running-tests)
- [Submitting a Pull Request](#submitting-a-pull-request)
- [Reporting Issues](#reporting-issues)
- [Project Architecture](#project-architecture)
- [Legal](#legal)

## Getting Started

### Prerequisites

| Requirement | Version | Notes |
|-------------|---------|-------|
| [Python](https://www.python.org/) | **3.10+** | 3.14 recommended (matches the workspace) |
| [pip](https://pip.pypa.io/) | recent | Or `uv` if you prefer |
| [Git](https://git-scm.com/) | **2.x+** | For cloning and version control |

### Development Setup

1. **Fork** the repository on GitHub.

2. **Clone** your fork locally:

   ```bash
   git clone https://github.com/<your-username>/toolsmith.git
   cd toolsmith
   ```

3. **Install** in editable mode with dev dependencies:

   ```bash
   pip install -e ".[dev]"
   ```

4. **Verify** your environment:

   ```bash
   python -m pytest -q
   ```

## Making Changes

### Branching Strategy

All development is based on the `master` branch.

- Create a feature branch from `master`:

  ```bash
  git checkout -b feature/your-feature master
  ```

- Use descriptive branch names: `feature/add-xyz`, `fix/tally-skip-count`, `refactor/simplify-module-resolution`.

### Code Style

- Follow [PEP 8](https://peps.python.org/pep-0008/).
- Add type hints to public functions; target the language level in `pyproject.toml` (`requires-python`).
- Use Google-style docstrings (`Args:` / `Returns:`) - match the existing modules.
- Keep each tool's real logic in its library module (`toolsmith.gradle`, `toolsmith.tally`, ...); `toolsmith.server` stays a thin typed veneer that forwards to it.
- A new MCP tool should also be usable from the command line (a `main()` / `python -m` entry), so its logic is testable without the MCP layer.

### Commit Messages

Write commit messages in **imperative mood** (e.g., "Add compile-only default to gradle_verify" not "Added ...").

- Keep the subject line under 72 characters.
- Use the body to explain **why**, not just **what**.
- Reference issue numbers where applicable (e.g., `Fixes #12`).

### Running Tests

```bash
# Run the full suite
python -m pytest -q

# Run one test file
python -m pytest tests/test_imports.py -q

# Run one test
python -m pytest tests/test_imports.py::test_idempotent -q
```

New behavior needs a test. The reorderer in particular must stay **idempotent** and byte-faithful to the IntelliJ Default layout - add a fixture rather than loosen an assertion.

## Submitting a Pull Request

1. **Push** your branch to your fork:

   ```bash
   git push origin feature/your-feature
   ```

2. **Open a Pull Request** against the `master` branch of the upstream repository.

3. In the PR description:
   - Summarize the changes and motivation.
   - Reference any related issues.
   - Note any breaking changes to a tool's return shape.

4. **Respond to feedback** - maintainers may request changes before merging.

## Reporting Issues

- Use [GitHub Issues](../../issues) to report bugs or request features.
- For bugs, include: the tool and arguments, expected vs. actual result, and your Python / FastMCP versions.
- For a new tool request, describe the repeated operation it would replace and the ideal return shape.

## Project Architecture

This project is part of the [Simplified-Dev](https://github.com/simplified-dev) ecosystem. Unlike its sibling Java libraries, it is a local, executable MCP server (not a JitPack-published artifact), so it uses a Python `src/` layout:

```
src/toolsmith/    - server + one module per tool
tests/            - pytest suite
pyproject.toml    - packaging + the `toolsmith` entry point
```

## Legal

By contributing, you agree that your contributions will be licensed under the [Apache License 2.0](LICENSE.md), the same license that covers this project.
