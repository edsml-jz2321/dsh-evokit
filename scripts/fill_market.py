"""Fill the draft market's public source commit and publication timestamp."""

import argparse
import json
import re
import time
from pathlib import Path


def main() -> None:
    """Update one pinned Git entry after the public source has been pushed."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--timestamp", type=int, default=None)
    args = parser.parse_args()
    if not re.fullmatch(r"[0-9a-f]{40}", args.commit) or args.commit == "0" * 40:
        parser.error("--commit must be a nonzero full lowercase Git SHA")
    timestamp = int(time.time()) if args.timestamp is None else args.timestamp
    if timestamp <= 0:
        parser.error("--timestamp must be positive Unix seconds")
    path = Path(__file__).resolve().parents[1] / "market/index.json"
    index = json.loads(path.read_text(encoding="utf-8"))
    version = index["plugins"][0]["versions"][0]
    version["source"]["commit"] = args.commit
    version["publishTimestamp"] = timestamp
    path.write_text(json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("Filled market source commit and publication timestamp")


if __name__ == "__main__":
    main()
