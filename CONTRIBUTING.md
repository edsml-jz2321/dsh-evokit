# Contributing

Use Python 3.11+ and uv. Keep changes focused and include a regression test for
behavioral fixes. The public contract is documented in README.md and docs/;
docs/tools.md owns CLI/MCP inputs and results. Edit skills/ first, then copy each
skill to integrations/dsh/skills/ so the wheel and DSH bundle stay identical.

```console
uv sync --frozen --group dev
uv run --frozen ruff check src tests examples scripts integrations
uv run --frozen ruff format --check src tests examples scripts integrations
uv run --frozen pytest -q tests --basetemp=.test-tmp
uv build
uv run --frozen python scripts/check_package.py
npm pack ./integrations/dsh --ignore-scripts --pack-destination dist
uv run --frozen python integrations/dsh/check_package.py
```

For the complete optional backend suite, run `uv sync --frozen --group dev --extra hb`
and `uv run --frozen --extra hb pytest -q tests --basetemp=.test-tmp`.
Use a private temporary home for backend state; the test fixtures configure it.
All tests use synthetic data and local fake services. Never add real credentials,
private tasks, runtime state or paid model calls. Scenarios execute trusted code.
Keep local experiments in ignored directories; retain only reproducible evidence
that is safe to share. Record user-visible changes in CHANGELOG.md.

## Releasing

Pushing a tag `vX.Y.Z` builds the wheel, sdist and DSH bundle, and publishes the wheel and sdist to PyPI through Trusted Publishing (`release.yml`, environment `pypi`; no token is stored). Publish the DSH bundle to npm separately, and create the GitHub Release from the same build.
