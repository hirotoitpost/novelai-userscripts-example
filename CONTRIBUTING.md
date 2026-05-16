# Contributing to novelai-userscripts-example

English | [日本語](docs/CONTRIBUTING_jp.md)

Thank you for your interest in contributing! This document provides guidelines for contributing to the novelai-userscripts-example project.

## Quick Start Workflow

1. **Fork and Clone**: Fork the repository and clone to your local machine
   ```bash
   git clone https://github.com/YOUR_USERNAME/novelai-userscripts-example.git
   cd novelai-userscripts-example
   ```

2. **Create Branch**: Use the naming convention below
   ```bash
   git checkout -b feature/YOUR_DESCRIPTION
   ```

3. **Implement Changes**: Edit code, add tests, and update documentation

4. **Verify Quality**: Run all checks locally
   ```bash
   uv run poe fmt
   uv run poe lint
   uv run poe check
   pytest
   npm run lint  # JavaScript/React
   ```

5. **Commit**: Follow [Conventional Commits](https://www.conventionalcommits.org/) format
   ```bash
   git commit -m "feat: add new feature description"
   ```

6. **Push & Create PR**: Push your branch and create a pull request via GitHub Web UI

## Contribution Types

- **Bug Fixes**: Reference Issues, include test cases, detail the fix in PR description
- **New Features**: Propose via Issues/Discussions first, include tests, documentation, and examples
- **Documentation**: Improve clarity, add examples, fix typos, contribute translations
- **Code Quality**: Refactor, optimize, improve test coverage

## Development Environment

### Requirements

- Windows, macOS, or Linux
- Python 3.10 or higher
- Node.js 16 or higher
- uv (Python package manager)
- Git

### Setup

```bash
# Python environment
uv sync

# JavaScript dependencies
npm install

# Pre-commit hooks
uv run poe pre-commit

# Run all checks
uv run poe fmt && uv run poe lint && uv run poe check && pytest
```

## Code Standards

### Python

- **Formatter**: Ruff (`uv run poe fmt`)
- **Linter**: Ruff (`uv run poe lint`)
- **Type Checker**: Pyright (`uv run poe check`)
- **Style**: Follow PEP 8 conventions enforced by Ruff
- **Type Hints**: Full type annotations required (Python 3.10+)
- **Validation**: Use Pydantic v2 for data validation

### JavaScript/React

- **Formatter**: Prettier
- **Linter**: ESLint
- **Type System**: TypeScript
- **Module Format**: ES modules

### Markdown

- Maximum 100 characters per line
- Split files exceeding 500 lines into logical sections
- Use clear headings and code block syntax highlighting

### Language

- **Primary**: English for code, comments, and documentation
- **Secondary**: Japanese translations provided for key documents

## Git Workflow & Branch Strategy

### Branch Naming Convention

```
feature/[DESCRIPTION]    # New features
fix/[DESCRIPTION]        # Bug fixes
docs/[DESCRIPTION]       # Documentation updates
refactor/[DESCRIPTION]   # Code refactoring
test/[DESCRIPTION]       # Test additions/improvements
chore/[DESCRIPTION]      # Build, dependencies, etc.
```

Example: `feature/add-character-reference-system`, `fix/metadata-extraction-bug`

### Commit Message Format

Follow [Conventional Commits](https://www.conventionalcommits.org/):

```
{feat|fix|docs|style|refactor|test|chore}: Brief description

Optional body explaining:
- Why the change is necessary
- What the change does
- How to verify it works
```

Example:
```
feat: implement character reference system

Add support for maintaining consistent character appearances
across multiple generated images using ControlNet integration.

Fixes #42
```

## PR Checklist Requirements

Before submitting a pull request, verify:

- [ ] Branch is up to date with `main`
- [ ] No merge conflicts
- [ ] All tests passing: `pytest`
- [ ] Code formatted: `uv run poe fmt`
- [ ] Linting passed: `uv run poe lint`
- [ ] Type checking passed: `uv run poe check`
- [ ] JavaScript linting passed: `npm run lint`
- [ ] Documentation updated (README, docstrings, etc.)
- [ ] Commit messages follow [Conventional Commits](https://www.conventionalcommits.org/) format
- [ ] PR description references related Issues

## Documentation

- Update docstrings for all functions and classes
- Include usage examples in docstrings
- Update README.md if adding new features
- Update CHANGELOG.md in the Unreleased section

## Testing

- Write tests for all new functionality
- Maintain or improve code coverage
- Test file location: `tests/` directory
- Run tests: `pytest`
- Test naming convention: `test_[feature_name].py`

## Code Review Process

1. Request reviewers when submitting PR
2. Address review comments promptly
3. Re-request review after making changes
4. Ensure all conversations are resolved before merging
5. Squash commits if requested during review

## Before You Start

- Check existing Issues/PRs to avoid duplicates
- Discuss major changes in Issues or Discussions first
- Check the `docs/` directory for troubleshooting guides

## Support & Questions

- **Issues**: Bug reports and feature requests
- **Discussions**: Questions, ideas, and general discussion
- **Documentation**: Check docs/ directory for guides

## License

By contributing to this project, you agree that your contributions will be licensed under the MIT License.
