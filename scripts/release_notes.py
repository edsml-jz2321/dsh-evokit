"""Print the CHANGELOG section of one version, for the GitHub Release body.

Usage: python scripts/release_notes.py 0.1.8 [--changelog CHANGELOG.md]
Exits 1 if the section is missing or empty, so a release never ships blank notes.
"""

import argparse
import sys
from pathlib import Path


def section(text: str, version: str) -> str:
    """Return the body under `## v<version>` up to the next level-2 heading."""
    lines, body, inside = text.splitlines(), [], False
    for line in lines:
        if line.startswith("## "):
            if inside:
                break
            inside = line.strip() == f"## v{version}"
            continue
        if inside:
            body.append(line)
    return "\n".join(body).strip()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("version")
    parser.add_argument("--changelog", type=Path, default=Path("CHANGELOG.md"))
    options = parser.parse_args()
    notes = section(options.changelog.read_text(encoding="utf-8"), options.version)
    if not notes:
        sys.exit(f"No CHANGELOG section for v{options.version}")
    print(notes)
