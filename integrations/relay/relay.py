"""Compatibility CLI for the private wheel's generic accounting relay."""

from evokit.relay import Relay, cache_hints, clear_cache_markers, main, usage_cost

__all__ = ["Relay", "cache_hints", "clear_cache_markers", "usage_cost"]

if __name__ == "__main__":
    main()
