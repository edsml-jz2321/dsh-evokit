# evokit plugin index

This directory is the template of the format-1 DSH plugin index. The published
copy is `index.json` on the `gh-pages` branch, served at
https://edsml-jz2321.github.io/dsh-evokit/index.json, with `icon.svg` beside it.

The release workflow keeps the published copy current: after npm publishes a
tagged version, its `update-dsh-index` job runs

```console
python scripts/update_market.py pages/index.json --version X.Y.Z --timestamp NPM_PUBLISH_SECONDS
```

which replaces the single `versions` entry (npm source, `engines.dsh` from
integrations/dsh/package.json) and keeps every other byte. It then validates the
file with DSH's own `parseMarketIndex` (`scripts/validate_market.mjs`), pushes
gh-pages and waits until Pages serves the new version.

Edit titles, descriptions, keywords or the icon here and on gh-pages together;
the version entry in this template is only an example and is not updated per
release. Users must explicitly add the index URL in DSH; there is no built-in index.
