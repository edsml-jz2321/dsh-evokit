"""Visibility shared by record stores, lifecycle and operator portability."""

PRIVATE_SPLITS = frozenset({"holdout", "confirmation", "final"})
PRIVATE_EVENTS = frozenset(
    {
        "holdout_measurement",
        "confirmation",
        "confirmation_started",
        "final_open",
        "final_measurement",
        "epoch_contract",
        "revert",
    }
)


def private_event(kind: str, data: dict) -> bool:
    """Classify ledger events without relying on their physical backend."""
    return kind in PRIVATE_EVENTS or (kind == "run" and data["split"] in PRIVATE_SPLITS)
