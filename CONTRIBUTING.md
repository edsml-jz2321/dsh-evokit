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

Releases are fully automatic from a tag.

1. Bump the version in pyproject.toml, uv.lock, both plugin.json files, both
   package.json files and server.json, and add a `## vX.Y.Z` CHANGELOG section.
   `scripts/check_package.py` and the tests refuse mismatched versions.
2. Push the tag `vX.Y.Z`. `release.yml` builds the wheel, sdist and DSH bundle
   once, then publishes them:
   - `publish-pypi` uploads the wheel and sdist to PyPI (Trusted Publishing,
     environment `pypi`);
   - `publish-mcp-registry` runs after `publish-pypi`: it waits until PyPI serves
     the version with its `mcp-name` marker, then publishes server.json to the
     official MCP Registry with a pinned, checksum-verified `mcp-publisher` and
     GitHub OIDC login;
   - `publish-npm` publishes the built `dsh-evokit-X.Y.Z.tgz` to npm (Trusted
     Publishing, environment `npm`, npm 11.5.1 or later, provenance attached);
   - `github-release` creates the GitHub Release with the three artifacts,
     SHA256SUMS and the tag's CHANGELOG section, marked latest. Glama's
     Auto-Release rebuilds its listing from each GitHub release.
   - `update-dsh-index` runs after `publish-npm`: it reads the npm publish
     time, points the gh-pages `index.json` (the published DSH plugin index) at
     the new version, validates it with DSH's format-1 validator, pushes
     gh-pages, requests a Pages build and waits until
     https://edsml-jz2321.github.io/dsh-evokit/index.json serves the version.
     `market/index.json` on main is only the template; CI never pushes to main.

No token or secret is stored. A failed job fails the run without affecting the
others' results, and rerunning it is safe: PyPI skips files that exist, npm skips
a version already published with the same tarball, an existing GitHub Release is
updated in place, a version already in the MCP Registry is skipped, and an
index that already lists the version is left unchanged.

One-time setup (done for this repository):

- GitHub environments `pypi` and `npm`, each with a deployment rule limited to
  tags matching `v*`.
- PyPI trusted publisher for `dsh-evokit`: owner `edsml-jz2321`, repository
  `dsh-evokit`, workflow `release.yml`, environment `pypi`.
- npm trusted publisher for `dsh-evokit` (GitHub Actions): `edsml-jz2321`,
  repository `dsh-evokit`, workflow `release.yml`, environment `npm`, allowed to
  publish and stage-publish. Values are case-sensitive and npm validates them
  only at publish time.
- The MCP Registry needs no setup: GitHub OIDC login from this repository may
  publish names under `io.github.edsml-jz2321/`.
