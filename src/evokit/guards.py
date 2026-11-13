"""Public instance guards independent of private holdout membership."""

from __future__ import annotations

import difflib
import re


def literal_match(text: str, value: str, *, task_id: bool) -> bool:
    if not value:
        return False
    if task_id and (value.isdecimal() or len(value) <= 2):
        # Numeric/short ids overlap ordinary scalar settings. Only quoted references
        # count; explicitly declared literals can impose a stricter scenario policy.
        return re.search(r"(['\"])" + re.escape(value) + r"\1", text) is not None
    left = r"(?<!\w)" if value[0].isalnum() or value[0] == "_" else ""
    right = r"(?!\w)" if value[-1].isalnum() or value[-1] == "_" else ""
    return re.search(left + re.escape(value) + right, text) is not None


def introduced_literal(
    before: dict[str, bytes], after: dict[str, bytes], ids: list[str], literals: list[str]
) -> bool:
    chunks = []
    for name in sorted(before.keys() | after.keys()):
        if before.get(name) == after.get(name):
            continue
        if name not in before:
            chunks.append(name)
        old, new = before.get(name, b""), after.get(name, b"")
        try:
            chunks.extend(
                line[2:]
                for line in difflib.ndiff(old.decode().splitlines(), new.decode().splitlines())
                if line.startswith("+ ")
            )
        except UnicodeDecodeError:
            chunks.append(new.decode("utf-8", errors="replace"))
    text = "\n".join(chunks)
    return any(literal_match(text, value, task_id=True) for value in ids) or any(
        literal_match(text, value, task_id=False) for value in literals
    )
