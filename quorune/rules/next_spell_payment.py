from __future__ import annotations

"""Temporary player-rule shapes and the next spell payment window."""

from dataclasses import dataclass
from typing import Any, Mapping
from ..casting_payment_keywords import PAYMENT_GRANT_SCOPE_FIELD
from ..target_source import (
    TURN_SPELL_SOURCE_COLOR_PROTECTION_CAPABILITY, TURN_SPELL_SOURCE_COLOR_PROTECTION_MECHANIC,
    TURN_SPELL_SOURCE_COLOR_PROTECTION_OPERATION, TURN_SPELL_SOURCE_COLOR_PROTECTION_WINDOW,
    turn_spell_source_color_grant,
)
from ..turn_creature_target_prohibition_model import (
    TURN_CREATURE_TARGET_PROHIBITION_CAPABILITY, TURN_CREATURE_TARGET_PROHIBITION_MECHANIC,
    TURN_CREATURE_TARGET_PROHIBITION_WINDOW,
)
from ..qualified_hexproof import (
    QUALIFIED_HEXPROOF_CAPABILITY, TURN_COLOR_SPELL_PROTECTION_CAPABILITY, TURN_COLOR_SPELL_PROTECTION_MECHANIC,
    turn_color_spell_protection_colors, uses_turn_color_spell_protection,
)


NEXT_SPELL_PAYMENT_OPERATION = "grant_next_spell_payment"
NEXT_SPELL_PAYMENT_CAPABILITY = "casting.payment.next_spell_grant"
NEXT_SPELL_PAYMENT_MECHANIC = "fixed-next-spell-payment-grant"
NEXT_SPELL_PAYMENT_FIELD = "next_spell_payment"
_LEGACY_IMPROVISE_FIELD = "next_spell_improvise"
_KINDS = frozenset({"convoke", "improvise"})


def next_spell_payment_kind(effect: Mapping[str, Any], *, actor: str = "$controller") -> str:
    if (not isinstance(effect, Mapping) or set(effect) != {"op", "schema_version", "player", "kind"}
            or effect["op"] != NEXT_SPELL_PAYMENT_OPERATION
            or type(effect["schema_version"]) is not int or effect["schema_version"] != 1
            or effect["player"] != actor or type(effect["kind"]) is not str or effect["kind"] not in _KINDS):
        raise ValueError("Next spell payment requires a closed controller result")
    return effect["kind"]


def next_spell_payment_payload_dependencies(effect: Mapping[str, Any]) -> tuple[str, ...]:
    kind = next_spell_payment_kind(effect)
    return NEXT_SPELL_PAYMENT_CAPABILITY, f"casting.payment.{kind}"


def next_spell_payment_node_capabilities(*, effects, target_schema, mechanic_ids, **_kwargs):
    if target_schema is not None or len(effects) != 1 or NEXT_SPELL_PAYMENT_MECHANIC not in mechanic_ids:
        return ()
    try:
        return next_spell_payment_payload_dependencies(effects[0])
    except (TypeError, ValueError):
        return ()


def is_closed_next_spell_payment_program(program):
    required = next_spell_payment_node_capabilities(effects=program.effects,
        target_schema=program.target_schema, mechanic_ids=program.coverage)
    return bool(required) and set(required) <= set(program.capability_dependencies)


def _turn_color_spell_protection_pair(effects, target_schema):
    from ..public_history_query_model import OPPONENT_CAST_COLOR_DRAW, SPELL_COLOR_HISTORY_FIELD

    if target_schema is not None or len(effects) != 2:
        return ()
    draw, protection = effects
    try:
        colors = turn_color_spell_protection_colors(protection, player="$controller")
    except (TypeError, ValueError):
        return ()
    if (not isinstance(draw, Mapping) or set(draw) != {"op", "player", "colors", SPELL_COLOR_HISTORY_FIELD}
            or draw.get("op") != OPPONENT_CAST_COLOR_DRAW or draw.get("player") != "$controller"
            or type(draw.get(SPELL_COLOR_HISTORY_FIELD)) is not int or draw[SPELL_COLOR_HISTORY_FIELD] != 1
            or not isinstance(draw.get("colors"), (tuple, list)) or tuple(draw["colors"]) != colors):
        return ()
    return (TURN_COLOR_SPELL_PROTECTION_CAPABILITY, QUALIFIED_HEXPROOF_CAPABILITY, "zone.draw.library_to_hand")


def turn_color_spell_protection_node_capabilities(*, effects, target_schema, mechanic_ids):
    if TURN_COLOR_SPELL_PROTECTION_MECHANIC not in mechanic_ids:
        return ()
    return _turn_color_spell_protection_pair(effects, target_schema)


def turn_color_spell_protection_runtime_dependencies(program):
    from .effect_shape_walkers import nested_effects
    from ..public_history_query_model import uses_sealed_opponent_cast_colors

    if not any(uses_turn_color_spell_protection(effect) or uses_sealed_opponent_cast_colors(effect)
               for effect in nested_effects((program.effects, program.handlers))):
        return ()
    result = _turn_color_spell_protection_pair(program.effects, program.target_schema)
    if (not result or program.event != "resolve" or program.active_zone != "stack"
            or program.event_condition is not None or program.handlers
            or not program.ability_id.startswith("spell:")):
        raise ValueError("Turn-long color protection requires its ordered unconditional spell body")
    return result


def is_closed_turn_color_spell_protection_program(program):
    if TURN_COLOR_SPELL_PROTECTION_MECHANIC not in program.coverage:
        return False
    try:
        needed = turn_color_spell_protection_runtime_dependencies(program)
    except (TypeError, ValueError):
        return False
    return bool(needed) and set(needed).issubset(program.capability_dependencies)


def turn_spell_source_color_protection_node_capabilities(*, effects, target_schema, mechanic_ids):
    if target_schema is not None or len(effects) != 1 or TURN_SPELL_SOURCE_COLOR_PROTECTION_MECHANIC not in mechanic_ids:
        return ()
    try:
        turn_spell_source_color_grant(effects[0])
    except (TypeError, ValueError):
        return ()
    return (TURN_SPELL_SOURCE_COLOR_PROTECTION_CAPABILITY,)


def turn_spell_source_color_protection_runtime_dependencies(program):
    from .effect_shape_walkers import nested_effects

    if not any(effect.get("op") == TURN_SPELL_SOURCE_COLOR_PROTECTION_OPERATION
               for effect in nested_effects((program.effects, program.handlers, program.target_schema))):
        return ()
    required = turn_spell_source_color_protection_node_capabilities(
        effects=program.effects, target_schema=program.target_schema,
        mechanic_ids=(TURN_SPELL_SOURCE_COLOR_PROTECTION_MECHANIC,),
    )
    if (not required or program.event != "resolve" or program.active_zone != "stack"
            or program.event_condition is not None or program.handlers or not program.ability_id.startswith("spell:")):
        raise ValueError("Turn-long spell-source protection requires the complete non-targeted spell result")
    return required


def is_closed_turn_spell_source_color_protection_program(program):
    if TURN_SPELL_SOURCE_COLOR_PROTECTION_MECHANIC not in program.coverage:
        return False
    try:
        required = turn_spell_source_color_protection_runtime_dependencies(program)
    except (TypeError, ValueError):
        return False
    return bool(required) and set(required).issubset(program.capability_dependencies)


def temporary_player_rule_node_capabilities(**shape):
    from .cast_cascade_grants import cast_cascade_node_capabilities
    from .next_cast_counter_protection import next_cast_protection_node_capabilities
    from .temporary_cast_restrictions import temporary_cast_node_capabilities
    from ..draw_redirect_model import temporary_draw_redirect_node_capabilities
    from .next_draw_replacement_shapes import next_draw_replacement_node_capabilities
    from .event_spell_copy import next_cast_copy_node_capabilities
    from .temporary_cast_timing import temporary_cast_timing_node_capabilities
    from .land_play_permissions import turn_land_plays_node_capabilities
    from .all_player_turn_rules import all_player_turn_rule_node_capabilities
    from .turn_creature_target_prohibition import turn_creature_target_prohibition_node_capabilities

    return (next_spell_payment_node_capabilities(**shape) or cast_cascade_node_capabilities(**shape)
            or next_cast_protection_node_capabilities(**shape) or temporary_cast_node_capabilities(**shape)
            or temporary_draw_redirect_node_capabilities(**shape) or next_draw_replacement_node_capabilities(**shape)
            or next_cast_copy_node_capabilities(**shape) or temporary_cast_timing_node_capabilities(**shape)
            or turn_land_plays_node_capabilities(**shape) or turn_color_spell_protection_node_capabilities(**shape)
            or turn_spell_source_color_protection_node_capabilities(**shape)
            or turn_creature_target_prohibition_node_capabilities(**shape)
            or all_player_turn_rule_node_capabilities(**shape))


def temporary_player_rule_covered_mechanics(supplied):
    from .all_player_turn_rules import ALL_PLAYER_TURN_RULE_CAPABILITIES, ALL_PLAYER_TURN_RULE_MECHANIC
    from ..all_player_turn_rule_model import TURN_LAND_MANA_TRIGGER_CAPABILITY
    from ..draw_redirect_model import DRAW_REDIRECT_CAPABILITY, DRAW_REDIRECT_MECHANIC
    from ..next_draw_replacement_model import NEXT_DRAW_REPLACEMENT_CAPABILITY, NEXT_DRAW_REPLACEMENT_MECHANIC
    from .cast_cascade_grants import CAST_CASCADE_CAPABILITY, CAST_CASCADE_MECHANIC
    from .next_cast_counter_protection import NEXT_CAST_PROTECTION_CAPABILITY, NEXT_CAST_PROTECTION_MECHANIC

    from ..event_spell_copy_model import NEXT_CAST_COPY_CAPABILITY, NEXT_CAST_COPY_MECHANIC
    from .temporary_cast_timing import TEMPORARY_CAST_TIMING_CAPABILITY, TEMPORARY_CAST_TIMING_MECHANIC
    from .land_play_permissions import TURN_LAND_PLAY_ALLOWANCE_CAPABILITY, TURN_LAND_PLAY_ALLOWANCE_MECHANIC

    return {mechanic for capability, mechanic in ((NEXT_SPELL_PAYMENT_CAPABILITY, NEXT_SPELL_PAYMENT_MECHANIC),
        *((capability, ALL_PLAYER_TURN_RULE_MECHANIC) for capability in ALL_PLAYER_TURN_RULE_CAPABILITIES.values()),
        (TURN_LAND_MANA_TRIGGER_CAPABILITY, ALL_PLAYER_TURN_RULE_MECHANIC),
        (CAST_CASCADE_CAPABILITY, CAST_CASCADE_MECHANIC),
        (NEXT_CAST_PROTECTION_CAPABILITY, NEXT_CAST_PROTECTION_MECHANIC),
        (NEXT_CAST_COPY_CAPABILITY, NEXT_CAST_COPY_MECHANIC),
        (DRAW_REDIRECT_CAPABILITY, DRAW_REDIRECT_MECHANIC),
        (NEXT_DRAW_REPLACEMENT_CAPABILITY, NEXT_DRAW_REPLACEMENT_MECHANIC),
        (TEMPORARY_CAST_TIMING_CAPABILITY, TEMPORARY_CAST_TIMING_MECHANIC),
        (TURN_LAND_PLAY_ALLOWANCE_CAPABILITY, TURN_LAND_PLAY_ALLOWANCE_MECHANIC),
        (TURN_COLOR_SPELL_PROTECTION_CAPABILITY, TURN_COLOR_SPELL_PROTECTION_MECHANIC),
        (TURN_SPELL_SOURCE_COLOR_PROTECTION_CAPABILITY, TURN_SPELL_SOURCE_COLOR_PROTECTION_MECHANIC),
        (TURN_CREATURE_TARGET_PROHIBITION_CAPABILITY, TURN_CREATURE_TARGET_PROHIBITION_MECHANIC)) if capability in supplied}


def expire_temporary_cast_ability_grants(stats):
    from .cast_cascade_grants import expire_cast_cascade_grants
    from .next_cast_counter_protection import expire_next_cast_protection
    from .temporary_cast_timing import expire_temporary_cast_timing

    from .land_play_permissions import expire_turn_land_play_allowance
    from ..temporary_graveyard_escape import TEMPORARY_ESCAPE_STATS_FIELD

    stats.pop(TEMPORARY_ESCAPE_STATS_FIELD, None)
    expire_temporary_cast_timing(stats)
    expire_turn_land_play_allowance(stats)
    expire_next_spell_payments(stats)
    expire_cast_cascade_grants(stats)
    expire_next_cast_protection(stats)
    stats.pop(TURN_SPELL_SOURCE_COLOR_PROTECTION_WINDOW, None)
    stats.pop(TURN_CREATURE_TARGET_PROHIBITION_WINDOW, None)
    from .targeted_turn_target_prohibition import WINDOW

    stats.pop(WINDOW, None)


@dataclass(frozen=True, slots=True)
class NextSpellPaymentWindow:
    turn_sequence: int
    kinds: tuple[str, ...]

    def __post_init__(self) -> None:
        if (type(self.turn_sequence) is not int or self.turn_sequence < 0 or not self.kinds
                or any(type(kind) is not str or kind not in _KINDS for kind in self.kinds)
                or self.kinds != tuple(sorted(set(self.kinds)))):
            raise ValueError("Next spell payment window is malformed")

    def to_dict(self) -> dict[str, Any]:
        return {"schema_version": 1, "turn_sequence": self.turn_sequence, "kinds": list(self.kinds)}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> NextSpellPaymentWindow:
        if (not isinstance(value, Mapping) or set(value) != {"schema_version", "turn_sequence", "kinds"}
                or type(value["schema_version"]) is not int or value["schema_version"] != 1
                or not isinstance(value["kinds"], list)):
            raise ValueError("Next spell payment window fields are closed")
        return cls(value["turn_sequence"], tuple(value["kinds"]))


def _current_payment_kinds(stats, turn_sequence):
    if NEXT_SPELL_PAYMENT_FIELD not in stats:
        return ()
    window = NextSpellPaymentWindow.from_dict(stats[NEXT_SPELL_PAYMENT_FIELD])
    if window.turn_sequence > turn_sequence:
        raise ValueError("Next spell payment window belongs to a future turn")
    return window.kinds if window.turn_sequence == turn_sequence else ()


def grant_next_spell_payment(stats, turn_sequence, kind):
    current = _current_payment_kinds(stats, turn_sequence)
    stats[NEXT_SPELL_PAYMENT_FIELD] = NextSpellPaymentWindow(
        turn_sequence, tuple(sorted({*current, kind})),
    ).to_dict()


def with_next_spell_payments(stats, turn_sequence, mechanics):
    current = _current_payment_kinds(stats, turn_sequence)
    granted = set(current)
    if stats.get(_LEGACY_IMPROVISE_FIELD):
        granted.add("improvise")
    if not granted:
        return mechanics
    existing = {str(value.get("kind") or "").casefold() for value in mechanics}
    payments = [*mechanics, *({"kind": kind, "schema_version": 1} for kind in sorted(granted - existing))]
    return [{**value, PAYMENT_GRANT_SCOPE_FIELD: "next_spell"} if value.get("kind") in current else value
            for value in payments]


def capture_next_spell_payments(stats, turn_sequence, *, preparing):
    kinds = _current_payment_kinds(stats, turn_sequence)
    legacy_improvise = bool(stats.get(_LEGACY_IMPROVISE_FIELD, False))
    if not preparing:
        expire_next_spell_payments(stats)
    return legacy_improvise, kinds


def expire_next_spell_payments(stats):
    stats.pop(_LEGACY_IMPROVISE_FIELD, None)
    stats.pop(NEXT_SPELL_PAYMENT_FIELD, None)
