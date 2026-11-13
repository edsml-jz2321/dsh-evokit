"""Verify the packed DSH bundle contains only the portable/native plugin assets."""

import argparse
import json
import tarfile
from pathlib import Path

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path, nargs="?")
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    if args.archive is None:
        archives = list((root.parents[1] / "dist").glob("dsh-evokit-*.tgz"))
        assert len(archives) == 1, "Expected exactly one DSH bundle in dist"
        args.archive = archives[0]
    with tarfile.open(args.archive) as package:
        names = set(package.getnames())
        required = {
            "package.json",
            "plugin.json",
            "mcp.json",
            "cordis.patch.yml",
            "improver.patch.yml",
            "skills/evolve/SKILL.md",
            "skills/onboard/SKILL.md",
            "skills/meta/SKILL.md",
        }
        assert {"package/" + name for name in required} <= names
        assert all(
            name
            in {"package/" + item for item in required} | {"package/README.md", "package/LICENSE"}
            for name in names
        )
        for name in required:
            actual = package.extractfile("package/" + name).read()
            expected = (root / name).read_bytes()
            assert (
                (json.loads(actual) == json.loads(expected))
                if name.endswith(".json")
                else (actual == expected)
            ), name
    print("Packed native/portable manifests and exact evolve skill verified")
