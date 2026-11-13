"""Validation of the closed scenario YAML surface before adapter construction."""

from __future__ import annotations

from typing import Any

import jsonschema

from .storage import canonical


def validate_config(config: Any) -> dict[str, Any]:
    """Refuse unknown, unsupported or malformed scenario settings.

    Args:
        config (Any): Parsed YAML document. Python adapter options remain owned
            by the adapter constructor; command settings are a closed schema.

    Returns:
        dict[str, Any]: The validated mapping, without adding defaults.

    Raises:
        ValueError: Invalid configuration, before adapter construction or runs.
    """
    string = {"type": "string", "minLength": 1}
    strings = {"type": "array", "items": string}
    properties = {
        "workspace": string,
        "playbook": string,
        "seed": {"type": "integer"},
        "stratified": {"type": "boolean"},
        "splits": {
            "type": "object",
            "additionalProperties": False,
            "required": ["diagnosis", "selection"],
            "properties": {
                k: {"type": "number", "minimum": 0, "maximum": 1}
                for k in ("diagnosis", "selection", "confirmation", "holdout", "final")
            },
        },
        "split_assignments": {
            "type": "object",
            "additionalProperties": False,
            "required": ["diagnosis", "selection", "confirmation", "final"],
            "properties": {k: strings for k in ("diagnosis", "selection", "confirmation", "final")},
        },
        "storage": {
            "type": "object",
            "required": ["backend"],
            "additionalProperties": False,
            "properties": {
                "backend": {"enum": ["files", "hb"]},
                "workspace": string,
                "backends": {"type": "object"},
                "operator_backends": {"type": "object"},
            },
        },
    }
    python = isinstance(config, dict) and "adapter" in config
    if python:
        properties.update(adapter=string, options={"type": "object"})
        required = ["adapter"]
    else:
        properties.update(
            {
                "editable": {**strings, "minItems": 1},
                "tasks": {
                    "oneOf": [
                        string,
                        {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "additionalProperties": False,
                                "required": ["id", "payload"],
                                "properties": {
                                    "id": string,
                                    "payload": {},
                                    "group": {"type": "string"},
                                },
                            },
                        },
                    ]
                },
                "command": {"oneOf": [string, {**strings, "minItems": 1}]},
                "timeout_seconds": {"type": "number", "exclusiveMinimum": 0},
                "scorer_version": string,
                "primer": {"type": "string"},
                "noise_policy": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "multiplier": {"type": "number", "minimum": 0},
                        "observed_floor": {"type": "boolean"},
                    },
                },
                "literals": strings,
                "gold_strings": strings,
                "private_literals": strings,
                "budgets": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "latency_ratio": {"type": "number", "minimum": 0},
                        "max_seconds": {"type": "number", "minimum": 0},
                        **{
                            k: {
                                "type": "object",
                                "additionalProperties": {"type": "number", "minimum": 0},
                            }
                            for k in ("cost_ratio", "max_cost")
                        },
                    },
                },
            }
        )
        required = ["editable", "tasks", "command"]
    try:
        jsonschema.validate(
            config,
            {
                "type": "object",
                "additionalProperties": False,
                "required": required,
                "properties": properties,
            },
        )
        canonical(config)
    except (jsonschema.ValidationError, TypeError, ValueError) as exc:
        # Do not echo values: options can contain private deployment settings.
        location = ".".join(map(str, getattr(exc, "absolute_path", ()))) or "root"
        message = exc.message if isinstance(exc, jsonschema.ValidationError) else "non-JSON value"
        if isinstance(exc, jsonschema.ValidationError) and exc.validator != "additionalProperties":
            message = f"invalid {exc.validator}"
        raise ValueError(f"Invalid scenario config at {location}: {message}") from None
    fractions = config.get("splits")
    if fractions is not None:
        if ("confirmation" in fractions) == ("holdout" in fractions):
            raise ValueError("splits requires confirmation or holdout alias, never both")
        if abs(sum(fractions.values()) - 1) > 1e-9:
            raise ValueError("splits fractions must sum to 1")
    storage = config.get("storage", {})
    if storage.get("backend") == "files" and set(storage) != {"backend"}:
        raise ValueError("files storage accepts only backend")
    return config
