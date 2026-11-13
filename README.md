# evokit plugin index

This is a format-1 DSH plugin index for static hosting. It is a draft until the
publisher fills its commit and timestamp. The zero SHA and timestamp deliberately
do not identify an installable release.

After the approved public push, run from the repository root:

```console
python scripts/fill_market.py --commit FULL_PUBLIC_COMMIT_SHA
```

The script sets the Unix publish time to now unless `--timestamp` is supplied.
Validate with upstream's `parseMarketIndex` before deploying this directory.
Deploy the contents of market/ as the static site root, so icon.svg resolves
beside index.json. The release guide lives in the publisher's external review.
Users must explicitly add the index URL in DSH; there is no built-in index.
