# Contributing to Chinese Coreference Resolution

Thank you for your interest in contributing! This document provides guidelines and instructions for contributing to this project.

感谢您有兴趣为本项目做贡献！本文档提供了贡献指南和说明。

## 📋 Table of Contents

- [Code of Conduct](#code-of-conduct)
- [Getting Started](#getting-started)
- [Development Setup](#development-setup)
- [Making Changes](#making-changes)
- [Testing](#testing)
- [Submitting Changes](#submitting-changes)
- [Style Guidelines](#style-guidelines)

## Code of Conduct

Please be respectful and constructive in all interactions. We welcome contributors of all backgrounds and experience levels.

## Getting Started

1. **Fork the repository** on GitHub
2. **Clone your fork** locally:
   ```bash
   git clone https://github.com/FlowElement-xinliuyuansu/m_flow.git
   cd chinese-coref
   ```
3. **Add the upstream remote**:
   ```bash
   git remote add upstream https://github.com/FlowElement-xinliuyuansu/m_flow.git
   ```

## Development Setup

### Prerequisites

- Python 3.9 or higher
- pip or pnpm (for frontend if applicable)

### Installation

```bash
# Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install with development dependencies
pip install -e ".[dev]"
```

### Verify Installation

```bash
# Run tests to ensure everything works
pytest tests/ -v
```

## Making Changes

### Branch Naming

- `feature/description` - New features
- `fix/description` - Bug fixes
- `docs/description` - Documentation updates
- `refactor/description` - Code refactoring

### Workflow

1. **Create a new branch**:
   ```bash
   git checkout -b feature/your-feature-name
   ```

2. **Make your changes** with clear, atomic commits

3. **Keep your branch updated**:
   ```bash
   git fetch upstream
   git rebase upstream/main
   ```

## Testing

### Running Tests

```bash
# Run all tests
pytest tests/ -v

# Run specific test file
pytest tests/test_coref_golden.py -v

# Run with coverage
pytest tests/ --cov=coreference_module --cov-report=html
```

### Test Requirements

- **All 85 existing tests must pass** before submitting a PR
- New features should include corresponding tests
- Bug fixes should include regression tests

### Writing Tests

Tests are located in the `tests/` directory:

```python
# tests/test_your_feature.py
import pytest
from coreference_module import CoreferenceResolver

class TestYourFeature:
    def test_basic_case(self):
        resolver = CoreferenceResolver()
        result, _ = resolver.resolve_text("your test input")
        assert "expected output" in result
```

## Submitting Changes

### Before Submitting

- [ ] All tests pass (`pytest tests/ -v`)
- [ ] Code follows style guidelines
- [ ] Comments are in English
- [ ] Documentation is updated if needed

### Pull Request Process

1. **Push your branch**:
   ```bash
   git push origin feature/your-feature-name
   ```

2. **Open a Pull Request** on GitHub

3. **Fill in the PR template** with:
   - Description of changes
   - Related issue numbers
   - Test results

4. **Wait for review** and address any feedback

### PR Title Format

```
type: brief description

Examples:
- feat: add support for demonstrative pronouns
- fix: correct antecedent selection for possessive pronouns
- docs: update API documentation
- refactor: extract scoring logic to separate module
```

## Style Guidelines

### Python Code Style

- Follow PEP 8 guidelines
- Use meaningful variable and function names
- Maximum line length: 100 characters
- Use type hints where appropriate

```python
# Good
def resolve_pronoun(pronoun: str, context: str) -> Optional[str]:
    """Resolve a pronoun to its antecedent."""
    ...

# Avoid
def rp(p, c):
    ...
```

### Comments and Documentation

- **All comments must be in English** (for international accessibility)
- Use docstrings for functions and classes
- Keep comments concise and meaningful

```python
def _find_replacement(self, pronoun: str, ...) -> Optional[str]:
    """
    Find the appropriate replacement for a pronoun.
    
    Args:
        pronoun: The pronoun to resolve
        ...
    
    Returns:
        The replacement text, or None if no resolution possible
    """
```

### Commit Messages

- Use clear, descriptive commit messages
- Start with a verb in imperative mood
- Keep the first line under 72 characters

```
Good:
- Add support for event pronouns
- Fix incorrect gender matching in possessive resolution
- Update README with new API examples

Avoid:
- Fixed stuff
- WIP
- asdfasdf
```

## Questions?

If you have questions or need help:

1. Check existing [Issues](https://github.com/FlowElement-xinliuyuansu/m_flow/issues)
2. Open a new issue with the "question" label
3. Contact us at contact@xinliuyuansu.com

---

Thank you for contributing! 🎉
