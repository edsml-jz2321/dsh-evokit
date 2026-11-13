"""Stable semantic code fingerprints independent of interpreter serialization state."""

from pathlib import PurePosixPath, PureWindowsPath
from types import CodeType
from typing import Any

from .storage import canonical, digest

# Explicit compatibility for the reviewed, released library module only. User
# sources and callable fingerprints still compare exactly; epochs are not edited.
V012_SCENARIO_SHA256 = "e32777d2bbfda29198816d2a2556769e15fed79da6372298b148fd1fe831f6a6"
V013_SCENARIO_SHA256 = "54a0a837bf5e6131cc1b5b79d8fb68dc05c7bf0cc83379206f5c2541d9005d1f"


V015_SCENARIO_SHA256 = "329260510f85b5f42cdd1d8b54c7baf1e0c9c93e41f3f4ce7bbf1ccebad71e62"


def compatible_v012_sources(expected: dict[str, str]) -> dict[str, str]:
    """Translate the released default module anchor without changing epoch data.

    Args:
        expected (dict[str, str]): Persisted source and callable anchors.

    Returns:
        dict[str, str]: Detached expected anchors; only the known v0.1.2 library
            source digest can advance. v0.1.2 requires its original default callback.
    """
    known = {V013_SCENARIO_SHA256}
    if "callable:secondary_guards" not in expected:
        known.add(V012_SCENARIO_SHA256)
    return {
        k: V015_SCENARIO_SHA256
        if v in known
        and not k.startswith(("source:", "callable:"))
        and (PureWindowsPath(k).name if "\\" in k else PurePosixPath(k).name) == "scenario.py"
        else v
        for k, v in expected.items()
    }


def code_fingerprint(code: CodeType) -> str:
    def constant(value: Any) -> Any:
        if isinstance(value, CodeType):
            return {"code": code_fingerprint(value)}
        if isinstance(value, bytes):
            return {"bytes": value.hex()}
        if isinstance(value, complex):
            return {"complex": [value.real.hex(), value.imag.hex()]}
        if isinstance(value, float):
            return {"float": value.hex()}
        if isinstance(value, tuple):
            return {"tuple": [constant(item) for item in value]}
        if isinstance(value, frozenset):
            return {"frozenset": sorted((constant(item) for item in value), key=canonical)}
        if value is Ellipsis:
            return {"ellipsis": True}
        return value

    return digest(
        {
            "bytecode": code.co_code.hex(),
            "constants": [constant(v) for v in code.co_consts],
            "names": code.co_names,
            "locals": code.co_varnames,
            "free": code.co_freevars,
            "cells": code.co_cellvars,
            "args": code.co_argcount,
            "positional_only": code.co_posonlyargcount,
            "keyword_only": code.co_kwonlyargcount,
            "flags": code.co_flags,
            "exceptions": code.co_exceptiontable.hex(),
        }
    )


def scorer_sources_match(
    expected: dict[str, str], actual: dict[str, str], *, owned_source: str | None = None
) -> bool:
    """Accept relocation of evokit's own scenario module with identical bytes.

    All user adapter paths, scenario-relative files and callable fingerprints
    still match exactly. Only the explicitly named installed evokit module may
    match a legacy absolute location. Arbitrary adapter relocation is unsafe:
    byte-identical code can read different support relative to __file__.
    """
    if expected == actual:
        return True
    if owned_source is None or owned_source not in actual or owned_source in expected:
        return False

    def basename(path: str) -> str:
        return PureWindowsPath(path).name if "\\" in path else PurePosixPath(path).name

    candidates = [
        key
        for key, checksum in expected.items()
        if key not in actual
        and not key.startswith(("source:", "callable:"))
        and basename(key) == basename(owned_source)
        and checksum == actual[owned_source]
    ]
    if len(candidates) != 1:
        return False
    relocated = {key: value for key, value in actual.items() if key != owned_source}
    relocated[candidates[0]] = actual[owned_source]
    return expected == relocated
