"""Point a DSH plugin index (format 1) at one published npm version.

Usage: python scripts/update_market.py INDEX --version 0.1.8 --timestamp 1795000000
       [--engines ">=0.2.2-rc.1 <0.3.0-0"] [--package integrations/dsh/package.json]

Only the single `versions` entry of the `dsh-evokit` plugin changes; every other
byte of the file is kept (the file is stored as two-space JSON with a final
newline). `--engines` defaults to `engines.dsh` of the bundle's package.json.
Run the upstream format-1 validator on the result before publishing it.
"""

import argparse
import json
import re
from pathlib import Path

NAME = "dsh-evokit"
SEMVER = re.compile(r"\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?")


def render(index: dict) -> str:
    """Serialize exactly as the stored index: two-space JSON, final newline."""
    return json.dumps(index, ensure_ascii=False, indent=2) + "\n"


def update(text: str, version: str, timestamp: int, engines: str) -> str:
    """Return the index text with one npm version entry for this plugin.

    Raises:
        ValueError: For a malformed index, version or timestamp, or non-ASCII text.
    """
    if not SEMVER.fullmatch(version) or type(timestamp) is not int or timestamp <= 0:
        raise ValueError("Need a SemVer version and positive Unix seconds")
    index = json.loads(text)
    if render(index) != text:
        raise ValueError("Index is not in canonical two-space form; refusing to rewrite it")
    if index.get("format") != 1:
        raise ValueError("Only format 1 indexes are supported")
    (plugin,) = [item for item in index["plugins"] if item["name"] == NAME]
    plugin["versions"] = [
        {
            "version": version,
            "publishTimestamp": timestamp,
            "source": {"type": "npm"},
            "engines": {"dsh": engines},
        }
    ]
    result = render(index)
    if not result.isascii():
        raise ValueError("Index must be English-only ASCII")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("index", type=Path)
    parser.add_argument("--version", required=True)
    parser.add_argument("--timestamp", type=int, required=True)
    parser.add_argument("--engines")
    parser.add_argument("--package", type=Path, default=Path("integrations/dsh/package.json"))
    options = parser.parse_args()
    package = json.loads(options.package.read_text(encoding="utf-8"))
    if package["name"] != NAME or package["version"] != options.version:
        raise SystemExit(f"{options.package} is not {NAME} {options.version}")
    engines = options.engines or package["engines"]["dsh"]
    text = options.index.read_text(encoding="utf-8")
    updated = update(text, options.version, options.timestamp, engines)
    options.index.write_text(updated, encoding="utf-8", newline="\n")
    print("changed" if updated != text else "unchanged")
