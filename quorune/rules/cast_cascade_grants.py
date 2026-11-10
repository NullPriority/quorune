from __future__ import annotations

"""Turn-bound effects that attach Cascade at spell announcement."""

from collections import Counter
from collections.abc import Mapping
from enum import StrEnum


CAST_CASCADE_OPERATION = "grant_cast_cascade"
CAST_CASCADE_CAPABILITY = "casting.ability.temporary_cascade_grant"
CAST_CASCADE_MECHANIC = "temporary-cast-cascade-grant"
CAST_CASCADE_RULES = ("514.2", "601.2a", "601.2i", "611.2a", "611.2f", "702.85a", "702.85c", "733")
CAST_CASCADE_WINDOW = "cast_cascade_grants"
_CAST_CASCADE_BINDING = "announced_cascade_grants"


class CastCascadeScope(StrEnum):
    CREATURE = "creature"
    NONCREATURE = "noncreature"
    INSTANT = "instant"
    SORCERY = "sorcery"
    HAND = "hand_this_turn"

    def matches(self, origin: str, types: set[str]) -> bool:
        if self is CastCascadeScope.HAND:
            return origin == "hand"
        if self is CastCascadeScope.NONCREATURE:
            return "creature" not in types
        return self.value in types


_EFFECT_SCOPES = (
    (CastCascadeScope.CREATURE,),
    (CastCascadeScope.NONCREATURE,),
    (CastCascadeScope.INSTANT, CastCascadeScope.SORCERY),
    (CastCascadeScope.HAND,),
)


def _decode_scopes(raw) -> tuple[CastCascadeScope, ...]:
    if not isinstance(raw, (list, tuple)) or not raw or any(type(value) is not str for value in raw):
        raise ValueError("Cast Cascade grants require closed spell scopes")
    return tuple(CastCascadeScope(value) for value in raw)


def cast_cascade_effect_scopes(effect, *, actor="$controller") -> tuple[CastCascadeScope, ...]:
    if (not isinstance(effect, Mapping) or set(effect) != {"op", "schema_version", "player", "scopes"}
            or effect["op"] != CAST_CASCADE_OPERATION or type(effect["schema_version"]) is not int
            or effect["schema_version"] != 1 or effect["player"] != actor):
        raise ValueError("Temporary Cascade requires a closed controller result")
    scopes = _decode_scopes(effect["scopes"])
    if scopes not in _EFFECT_SCOPES:
        raise ValueError("Unsupported temporary Cascade scope combination")
    return scopes


def cast_cascade_payload_dependencies(effect):
    cast_cascade_effect_scopes(effect)
    return (CAST_CASCADE_CAPABILITY, "trigger.keyword.cascade")


def cast_cascade_node_capabilities(*, effects, target_schema, mechanic_ids, **_unused):
    if target_schema is not None or len(effects) != 1 or CAST_CASCADE_MECHANIC not in mechanic_ids:
        return ()
    try:
        return cast_cascade_payload_dependencies(effects[0])
    except (TypeError, ValueError):
        return ()


def is_closed_cast_cascade_program(program):
    required = cast_cascade_node_capabilities(effects=program.effects,
        target_schema=program.target_schema, mechanic_ids=program.coverage)
    return bool(required) and set(required) <= set(program.capability_dependencies)


def _active_scopes(stats, turn_sequence) -> tuple[CastCascadeScope, ...]:
    if CAST_CASCADE_WINDOW not in stats:
        return ()
    value = stats[CAST_CASCADE_WINDOW]
    if (not isinstance(value, Mapping) or set(value) != {"schema_version", "turn_sequence", "scopes"}
            or type(value["schema_version"]) is not int or value["schema_version"] != 1
            or type(value["turn_sequence"]) is not int or not 0 <= value["turn_sequence"] <= turn_sequence):
        raise ValueError("Temporary Cascade window is malformed")
    scopes = _decode_scopes(value["scopes"])
    return scopes if value["turn_sequence"] == turn_sequence else ()


def _store_scopes(stats, turn_sequence, scopes):
    if scopes:
        stats[CAST_CASCADE_WINDOW] = {"schema_version": 1, "turn_sequence": turn_sequence,
                                    "scopes": [scope.value for scope in scopes]}
    else:
        expire_cast_cascade_grants(stats)


def grant_cast_cascade(stats, turn_sequence, scopes):
    _store_scopes(stats, turn_sequence, (*_active_scopes(stats, turn_sequence), *scopes))


def expire_cast_cascade_grants(stats):
    stats.pop(CAST_CASCADE_WINDOW, None)


def reserve_cast_cascade_grants(host, proposal) -> tuple[CastCascadeScope, ...]:
    """Read matching instances before payment, keeping the player window intact."""
    active = _active_scopes(host.state.players[proposal.seat].stats, host.state.turn_sequence)
    if not active:
        return ()
    # The selected cast option can announce a different type, as with Bestow.
    types, _, _ = host._type_parts(proposal.type_line)
    return tuple(scope for scope in active if scope.matches(proposal.origin, types))


def commit_cast_cascade_grants(stats, turn_sequence, reserved):
    if not reserved:
        return
    active = _active_scopes(stats, turn_sequence)
    if Counter(reserved) - Counter(active):
        raise ValueError("A reserved Cascade grant changed before cast completion")
    remaining = list(active)
    for scope in reserved:
        if scope is not CastCascadeScope.HAND:
            remaining.remove(scope)
    _store_scopes(stats, turn_sequence, remaining)


def announced_cascade_context(reserved):
    return {_CAST_CASCADE_BINDING: {"schema_version": 1, "instances": len(reserved)}} if reserved else {}


def announced_cascade_instances(context) -> int:
    if _CAST_CASCADE_BINDING not in context:
        return 0
    value = context[_CAST_CASCADE_BINDING]
    if (not isinstance(value, Mapping) or set(value) != {"schema_version", "instances"}
            or type(value["schema_version"]) is not int or value["schema_version"] != 1
            or type(value["instances"]) is not int or value["instances"] < 1):
        raise ValueError("Announced Cascade grant binding is malformed")
    return value["instances"]
