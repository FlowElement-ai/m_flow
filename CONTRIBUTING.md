> [!IMPORTANT]
> **Note for contributors:** Always branch from `dev`. PRs targeting `main` directly will be rejected.

# Contributing to M-flow

Thank you for your interest in improving M-flow! This guide walks you through the contribution workflow — from local setup to pull-request review.

## Quick Links

| Resource | URL |
|----------|-----|
| Issue Tracker | <https://github.com/FlowElement-xinliuyuansu/m_flow/issues> |
| Email | <mailto:contact@xinliuyuansu.com> |

## How to Contribute

We welcome every kind of contribution:

- **Bug reports** — open an issue with a minimal reproducer
- **Feature proposals** — describe the use-case and expected behaviour
- **Documentation** — fix typos, add examples, improve clarity
- **Code & tests** — implement fixes, features, or new adapters
- **Code review** — comment on open PRs with constructive feedback

## Development Setup

### 1. Fork & Clone

```bash
git clone https://github.com/<you>/m_flow.git
cd m_flow
```

### 2. Install Dependencies

We recommend **uv** for fast, reproducible installs:

```bash
uv sync --dev --all-extras --reinstall
```

### 3. Create a Feature Branch

```bash
git checkout -b feat/my-awesome-feature dev
```

### 4. Run the Test Suite

```bash
# Unit tests (fast, no network)
PYTHONPATH=. uv run pytest m_flow/tests/unit/ -v

# Full suite (may need LLM keys in .env)
PYTHONPATH=. uv run pytest m_flow/tests/ -v
```

### 5. Lint & Format

```bash
uv run ruff check .
uv run ruff format .
```

Fix any issues before committing.

## Submitting a Pull Request

1. **Sign your commits** — we enforce the DCO (Developer Certificate of Origin):
   ```bash
   git commit -s -m "feat(graph): add temporal edge weighting"
   ```
2. **Push and open a PR against `dev`**:
   ```bash
   git push origin feat/my-awesome-feature
   ```
3. Fill in the PR template. Include:
   - What changed and why
   - How you tested it locally
   - Any impacts on MCP server or frontend

### PR Title Convention

We follow [Conventional Commits](https://www.conventionalcommits.org/):

| Prefix | Meaning |
|--------|---------|
| `feat` | New feature |
| `fix`  | Bug fix |
| `docs` | Documentation only |
| `refactor` | Code change that neither fixes a bug nor adds a feature |
| `test` | Adding or correcting tests |
| `chore` | Tooling, CI, build changes |

Example: `feat(retrieval): add hybrid vector+graph search`

## Issue Labels

| Label | Description |
|-------|-------------|
| `good first issue` | Great for newcomers |
| `bug` | Something is broken |
| `enhancement` | Feature request |
| `documentation` | Docs improvement |
| `help wanted` | Community help appreciated |

## Community Guidelines

- Be respectful and inclusive
- Be professional and constructive in all interactions
- Provide constructive feedback
- Ask questions — no question is too basic

## Contact

- **General questions**: GitHub Issues or email contact@xinliuyuansu.com
- **Security issues**: Please report privately via [GitHub Security Advisories](https://github.com/FlowElement-xinliuyuansu/m_flow/security/advisories/new). Do NOT open public issues for security vulnerabilities.

Thank you for helping make M-flow better!
