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

1. Bump the version in pyproject.toml, uv.lock, both plugin.json files, both
   package.json files and server.json, and add a `## vX.Y.Z` CHANGELOG section.
   `scripts/check_package.py` and the tests refuse mismatched versions.
2. Push the tag `vX.Y.Z`. `release.yml` builds the wheel, sdist and DSH bundle
   once, then, in three independent jobs:
   - `publish-pypi` uploads the wheel and sdist to PyPI (Trusted Publishing,
     environment `pypi`);
   - `publish-npm` publishes the built `dsh-evokit-X.Y.Z.tgz` to npm (Trusted
     Publishing, environment `npm`, npm 11.5.1 or later, provenance attached);
   - `github-release` creates the GitHub Release with the three artifacts,
     SHA256SUMS and the tag's CHANGELOG section, marked latest. Glama's
     Auto-Release rebuilds its listing from each GitHub release.
   No token is stored for any of them. A failed job fails the run; rerunning it
   is safe (existing PyPI files and an identical npm version are skipped, and an
   existing GitHub Release is updated in place).
3. Publish server.json to the official MCP Registry by hand, after PyPI has the
   new version: `mcp-publisher login github`, then `mcp-publisher publish` in
   the repository root.

One-time setup:

- GitHub environments `pypi` and `npm`, each with a deployment rule limited to
  tags matching `v*`.
- PyPI trusted publisher for `dsh-evokit`: owner `edsml-jz2321`, repository
  `dsh-evokit`, workflow `release.yml`, environment `pypi`.
- npm trusted publisher for `dsh-evokit` (package settings, Trusted publishing,
  GitHub Actions): organization or user `edsml-jz2321`, repository `dsh-evokit`,
  workflow filename `release.yml`, environment `npm`, with `npm publish`
  allowed. Values are case-sensitive and npm validates them only at publish
  time.
