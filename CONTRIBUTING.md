# Contributing to morch

Thank you for your interest in contributing to **morch**. This document
covers the basics for getting started.

## Development Setup

```bash
git clone https://github.com/enginrect/multi-agent-orchestrator.git
cd multi-agent-orchestrator
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Running Tests

```bash
pytest tests/ -v
```

All pull requests must pass the existing test suite. New features should
include tests.

## Code Style

- Python 3.11+ with type annotations
- Snake-case for all names (files, variables, functions)
- Domain-driven layout: `domain/`, `application/`, `infrastructure/`,
  `adapters/`
- Prefer small, single-responsibility modules

## Pull Requests

1. Fork the repository and create a feature branch
2. Make focused changes with clear commit messages
3. Add or update tests for your changes
4. Run `pytest tests/ -v` and confirm all tests pass
5. Open a pull request with a description of what changed and why

## Reporting Issues

Open a GitHub issue with:
- What you expected
- What happened instead
- Steps to reproduce (if applicable)
- Your Python version and OS

## License

By contributing, you agree that your contributions will be licensed
under the [Apache License 2.0](LICENSE).
