from __future__ import annotations

"""Read the authoritative cast-commit ledger without maintaining another history."""

from collections.abc import Mapping


def _commander_cast_ledger(state, controller: str) -> Mapping[str, int]:
    if type(controller) is not str or controller not in state.players:
        raise ValueError("Commander cast count requires an existing controller")
    ledger = state.players[controller].commander_casts
    if (not isinstance(ledger, Mapping)
            or any(type(key) is not str or not key or type(value) is not int or value < 0
                   for key, value in ledger.items())):
        raise ValueError("Commander cast ledger requires named nonnegative integer counts")
    return ledger


def controller_commander_cast_count(state, controller: str) -> int:
    return sum(_commander_cast_ledger(state, controller).values())


def commander_cast_count(state, controller: str, oracle_id: str) -> int:
    if type(oracle_id) is not str or not oracle_id:
        raise ValueError("Commander cast count requires a named commander")
    return _commander_cast_ledger(state, controller).get(oracle_id, 0)
