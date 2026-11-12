from __future__ import annotations

"""Controller-bound instant timing for same-turn spell and creature-card play."""

from collections.abc import Mapping
from enum import StrEnum

from ..cast_timing import CastTimingPermission, type_line_has_card_type
from ..errors import GameRuleError


TEMPORARY_CAST_TIMING_OPERATION = "grant_cast_permission"
TEMPORARY_CAST_TIMING_FIELD = "temporary_cast_timing"
TEMPORARY_CAST_TIMING_CAPABILITY = "timing.cast.temporary_controller_permission"
NEXT_CREATURE_CARD_TIMING_CAPABILITY = "timing.play.next_creature_card"
TEMPORARY_CAST_TIMING_MECHANIC = "temporary-controller-cast-timing"


class TemporaryCastTimingScope(StrEnum):
    SPELLS = "spells"
    CREATURE_SPELLS = "creature_spells"
    SORCERY_SPELLS = "sorcery_spells"
    NEXT_SORCERY = "next_sorcery"
    NEXT_CREATURE_CARD = "next_creature_card"

    def matches(self, type_line: str, *, physical_card: bool = True) -> bool:
        if self is self.NEXT_CREATURE_CARD:
            return physical_card and type_line_has_card_type(type_line, "creature")
        if type_line_has_card_type(type_line, "land"):
            return False
        if self is self.SPELLS:
            return True
        card_type = "creature" if self is self.CREATURE_SPELLS else "sorcery"
        return type_line_has_card_type(type_line, card_type)


def temporary_cast_timing_effect_scope(effect, *, actor="$controller") -> TemporaryCastTimingScope:
    if (not isinstance(effect, Mapping)
            or set(effect) != {"op", "schema_version", "player", TEMPORARY_CAST_TIMING_FIELD}
            or effect["op"] != TEMPORARY_CAST_TIMING_OPERATION
            or type(effect["schema_version"]) is not int or effect["schema_version"] != 1
            or effect["player"] != actor or type(effect[TEMPORARY_CAST_TIMING_FIELD]) is not str):
        raise ValueError("Temporary cast timing requires a closed controller result")
    return TemporaryCastTimingScope(effect[TEMPORARY_CAST_TIMING_FIELD])


def temporary_cast_timing_payload_dependencies(effect):
    scope = temporary_cast_timing_effect_scope(effect)
    if scope is TemporaryCastTimingScope.NEXT_CREATURE_CARD:
        return (TEMPORARY_CAST_TIMING_CAPABILITY, NEXT_CREATURE_CARD_TIMING_CAPABILITY)
    return (TEMPORARY_CAST_TIMING_CAPABILITY,)


def temporary_cast_timing_node_capabilities(*, effects, target_schema, mechanic_ids, **_unused):
    if target_schema is not None or len(effects) != 1 or TEMPORARY_CAST_TIMING_MECHANIC not in mechanic_ids:
        return ()
    try:
        return temporary_cast_timing_payload_dependencies(effects[0])
    except (TypeError, ValueError):
        return ()


def is_closed_temporary_cast_timing_program(program):
    required = temporary_cast_timing_node_capabilities(
        effects=program.effects, target_schema=program.target_schema, mechanic_ids=program.coverage,
    )
    return bool(required) and set(required) <= set(program.capability_dependencies)


def _active_scopes(stats, turn_sequence) -> tuple[TemporaryCastTimingScope, ...]:
    if TEMPORARY_CAST_TIMING_FIELD not in stats:
        return ()
    window = stats[TEMPORARY_CAST_TIMING_FIELD]
    if (not isinstance(window, Mapping) or set(window) != {"schema_version", "turn_sequence", "scopes"}
            or type(window["schema_version"]) is not int or window["schema_version"] != 1
            or type(window["turn_sequence"]) is not int or not 0 <= window["turn_sequence"] <= turn_sequence):
        raise ValueError("Temporary cast timing window is malformed")
    scopes = window["scopes"]
    if (not isinstance(scopes, (tuple, list)) or not scopes or any(type(scope) is not str for scope in scopes)
            or tuple(scopes) != tuple(sorted(set(scopes)))):
        raise ValueError("Temporary cast timing scopes must be canonical")
    decoded = tuple(TemporaryCastTimingScope(scope) for scope in scopes)
    return decoded if window["turn_sequence"] == turn_sequence else ()


def _store_scopes(stats, turn_sequence, scopes):
    if scopes:
        stats[TEMPORARY_CAST_TIMING_FIELD] = {
            "schema_version": 1, "turn_sequence": turn_sequence,
            "scopes": sorted({scope.value for scope in scopes}),
        }
    else:
        expire_temporary_cast_timing(stats)


def has_next_creature_card_timing(stats, turn_sequence) -> bool:
    return TemporaryCastTimingScope.NEXT_CREATURE_CARD in _active_scopes(stats, turn_sequence)


def expire_temporary_cast_timing(stats):
    stats.pop(TEMPORARY_CAST_TIMING_FIELD, None)


def apply_temporary_cast_timing(host, effect, *, actor, reason):
    if actor not in host.active_seats:
        raise GameRuleError("Temporary cast timing requires the active ability controller")
    stats = host.state.players[actor].stats
    turn_sequence = host.state.turn_sequence
    try:
        scope = temporary_cast_timing_effect_scope(
            {key: value for key, value in effect.items() if key not in {"reason", "_runtime_source"}}, actor=actor,
        )
        _store_scopes(stats, turn_sequence, (*_active_scopes(stats, turn_sequence), scope))
    except (TypeError, ValueError) as exc:
        raise GameRuleError(str(exc)) from exc
    host._log(actor, "permission.temporary_cast_timing", f"{actor} gains a temporary casting timing permission.",
              {"player": actor, "scope": scope.value, "reason": reason}, importance=1, changed_players=[actor])
    return True


def temporary_cast_timing_permissions(host, actor, card, *, face=None) -> tuple[CastTimingPermission, ...]:
    scopes = _active_scopes(host.state.players[actor].stats, host.state.turn_sequence)
    if not scopes:
        return ()
    data = face if face is not None else host._effective_card_data(card)
    type_line = str(data.get("type_line") or "")
    if any(scope.matches(type_line, physical_card=card.object_kind == "card") for scope in scopes):
        return (CastTimingPermission(),)
    return ()


def consume_temporary_play_timing(stats, turn_sequence, *, type_line, object_kind):
    scopes = _active_scopes(stats, turn_sequence)
    one_shot = {TemporaryCastTimingScope.NEXT_SORCERY, TemporaryCastTimingScope.NEXT_CREATURE_CARD}
    remaining = tuple(scope for scope in scopes if not (
        scope in one_shot and scope.matches(type_line, physical_card=object_kind == "card")
    ))
    if remaining != scopes:
        _store_scopes(stats, turn_sequence, remaining)
