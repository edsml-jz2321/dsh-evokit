"""Completed, domain-validated response caching independent of provider transport."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

from evokit.storage import atomic_json, file_lock


class IncompleteResponse(ValueError):
    """Provider did not retain explicit successful stop metadata."""

    def __init__(self, message, content=""):
        super().__init__(message)
        self.content = content


class ResponseValidationError(ValueError):
    """An accounted complete response violates its owning contract."""

    def __init__(self, message, response):
        super().__init__(message)
        self.response = response


class ResponseCache:
    """Cache only stopped responses accepted by the current owning validator.

    Args:
        root: Optional private cache path. No provider headers belong in records.

    Transport dispatch and money settlement remain the caller's responsibility.
    """

    def __init__(self, root: Path | None):
        self.root = root
        self.replayed_usage = []

    def _decode(self, saved, decode, validator):
        if saved.get("finish_reason") != "stop":
            raise IncompleteResponse(
                "response is incomplete or lacks stop metadata", saved.get("content", "")
            )
        value = decode(saved["content"])
        if validator is not None:
            try:
                validator(value)
            except (ValueError, KeyError, TypeError, SyntaxError) as error:
                raise ResponseValidationError(str(error), value) from error
        return value

    def _diagnostic(self, path, saved, category, error=None):
        fields = {**saved, "validated": False}
        if error is not None:
            fields["validation_error"] = type(error).__name__ + ": " + str(error)
        atomic_json(path.parent / category / (path.stem + "-" + uuid.uuid4().hex + ".json"), fields)

    def complete(self, key, dispatch, decode, validator=None):
        """Resolve a response under its identity and current domain validator.

        Args:
            key: Hex request fingerprint, independent of wire cache markers.
            dispatch: Returns a sanitized response envelope after settlement.
            decode: Content parser raising ValueError on invalid model text.
            validator: Current assignment/domain contract, raising on rejection.

        Returns:
            object: Accepted parsed content. Replays incur no new usage.

        Raises:
            IncompleteResponse: Missing or non-stop completion metadata.
            ResponseValidationError: Assignment contract rejected the content.
            ValueError: Content is invalid. All rejected records are quarantined.
        """
        if len(key) != 64 or any(c not in "0123456789abcdef" for c in key):
            raise ValueError("Invalid response fingerprint")
        if self.root is None:
            return self._decode(dispatch(), decode, validator)
        path = self.root / (key + ".json")
        with file_lock(path.with_suffix(".lock"), timeout=600):
            if validator is not None and path.exists():
                saved = {}
                try:
                    saved = json.loads(path.read_text(encoding="utf-8"))
                    if not isinstance(saved, dict):
                        saved = {"invalid_envelope": saved}
                        raise ValueError("cached response envelope must be an object")
                    value = self._decode(saved, decode, validator)
                except (ValueError, KeyError, TypeError, SyntaxError) as error:
                    self._diagnostic(path, saved, "rejected", error)
                    path.unlink()
                else:
                    self.replayed_usage.append(saved.get("usage", []))
                    return value
            saved = dispatch()
            try:
                value = self._decode(saved, decode, validator)
            except (ValueError, KeyError, TypeError, SyntaxError) as error:
                self._diagnostic(path, saved, "rejected", error)
                raise
            if validator is None:
                self._diagnostic(path, saved, "unvalidated")
            else:
                atomic_json(path, {**saved, "validated": True})
            return value
