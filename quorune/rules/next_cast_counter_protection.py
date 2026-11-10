from __future__ import annotations

"""Closed next-cast counter protection using the existing counter owner."""

from collections import Counter
from collections.abc import Mapping
from enum import StrEnum


NEXT_CAST_PROTECTION_OPERATION = "next_spell_uncounterable"
NEXT_CAST_PROTECTION_CAPABILITY = "stack.counter.prohibition.next_cast"
NEXT_CAST_PROTECTION_MECHANIC = "next-cast-counter-protection"
NEXT_CAST_PROTECTION_RULES = ("101.2", "514.2", "601.2a", "608.2b", "608.2c", "611.2a", "611.2f", "701.6a")
NEXT_CAST_PROTECTION_WINDOW = "next_cast_counter_protection"
_ANNOUNCED_PROTECTION_FIELD = "announced_counter_protection"


class NextCastProtectionScope(StrEnum):
    ANY = "any"
    CREATURE = "creature"
    INSTANT_OR_SORCERY = "instant_or_sorcery"

    def matches(self, types: set[str]) -> bool:
        if self is NextCastProtectionScope.ANY:
            return True
        if self is NextCastProtectionScope.CREATURE:
            return "creature" in types
        return bool(types.intersection({"instant", "sorcery"}))


def is_versioned_next_cast_protection(value):
    return (value.get("op") == NEXT_CAST_PROTECTION_OPERATION
            and ("schema_version" in value or "scope" in value))


def next_cast_protection_effect_scope(effect, *, actor="$controller") -> NextCastProtectionScope:
    if (not isinstance(effect, Mapping) or set(effect) != {"op", "schema_version", "player", "scope"}
            or effect["op"] != NEXT_CAST_PROTECTION_OPERATION or type(effect["schema_version"]) is not int
            or effect["schema_version"] != 1 or effect["player"] != actor
            or type(effect["scope"]) is not str):
        raise ValueError("Next-cast protection requires a closed controller result")
    return NextCastProtectionScope(effect["scope"])


def next_cast_protection_payload_dependencies(effect):
    next_cast_protection_effect_scope(effect)
    return (NEXT_CAST_PROTECTION_CAPABILITY,)


def next_cast_protection_node_capabilities(*, effects, target_schema, mechanic_ids, **_unused):
    if target_schema is not None or len(effects) != 1 or NEXT_CAST_PROTECTION_MECHANIC not in mechanic_ids:
        return ()
    try:
        return next_cast_protection_payload_dependencies(effects[0])
    except (TypeError, ValueError):
        return ()


def is_closed_next_cast_protection_program(program):
    required = next_cast_protection_node_capabilities(effects=program.effects,
        target_schema=program.target_schema, mechanic_ids=program.coverage)
    return bool(required) and set(required) <= set(program.capability_dependencies)


def _active_scopes(stats, turn_sequence) -> tuple[NextCastProtectionScope, ...]:
    if NEXT_CAST_PROTECTION_WINDOW not in stats:
        return ()
    value = stats[NEXT_CAST_PROTECTION_WINDOW]
    if (not isinstance(value, Mapping) or set(value) != {"schema_version", "turn_sequence", "scopes"}
            or type(value["schema_version"]) is not int or value["schema_version"] != 1
            or type(value["turn_sequence"]) is not int or not 0 <= value["turn_sequence"] <= turn_sequence):
        raise ValueError("Next-cast protection window is malformed")
    scopes = value["scopes"]
    if (not isinstance(scopes, (list, tuple)) or not scopes or any(type(scope) is not str for scope in scopes)
            or tuple(scopes) != tuple(sorted(scopes))):
        raise ValueError("Next-cast protection scopes must be ordered")
    decoded = tuple(NextCastProtectionScope(scope) for scope in scopes)
    return decoded if value["turn_sequence"] == turn_sequence else ()


def _store_scopes(stats, turn_sequence, scopes):
    if scopes:
        stats[NEXT_CAST_PROTECTION_WINDOW] = {"schema_version": 1, "turn_sequence": turn_sequence,
            "scopes": sorted(scope.value for scope in scopes)}
    else:
        expire_next_cast_protection(stats)


def grant_next_cast_protection(stats, turn_sequence, scope: NextCastProtectionScope):
    _store_scopes(stats, turn_sequence, (*_active_scopes(stats, turn_sequence), scope))


def expire_next_cast_protection(stats):
    stats.pop(NEXT_CAST_PROTECTION_WINDOW, None)


def reserve_next_cast_protection(host, proposal) -> tuple[NextCastProtectionScope, ...]:
    active = _active_scopes(host.state.players[proposal.seat].stats, host.state.turn_sequence)
    if not active:
        return ()
    types, _, _ = host._type_parts(proposal.type_line)
    return tuple(scope for scope in active if scope.matches(types))


def commit_next_cast_protection(stats, turn_sequence, reserved):
    if not reserved:
        return
    active = _active_scopes(stats, turn_sequence)
    if Counter(reserved) - Counter(active):
        raise ValueError("A reserved counter protection changed before cast completion")
    remaining = list(active)
    for scope in reserved:
        remaining.remove(scope)
    _store_scopes(stats, turn_sequence, remaining)


def announced_protection_context(reserved):
    return {_ANNOUNCED_PROTECTION_FIELD: 1} if reserved else {}


def announced_cast_is_protected(item) -> bool:
    if item.kind != "spell" or _ANNOUNCED_PROTECTION_FIELD not in item.context:
        return False
    version = item.context[_ANNOUNCED_PROTECTION_FIELD]
    if type(version) is not int or version != 1:
        raise ValueError("Announced counter protection is malformed")
    return True
