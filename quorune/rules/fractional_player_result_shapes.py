from __future__ import annotations

"""Prove the half quantity separately from the existing player/selector shell."""

from collections.abc import Mapping

from ..fractional_quantity_model import (
    HalfQuantitySpec, HALF_LIFE_OPERATION, HALF_LIFE_MECHANIC, HALF_LIFE_CAPABILITY,
    HALF_CHOICE_MECHANIC, HALF_CHOICE_CAPABILITY,
    HALF_LIBRARY_DRAW_CAPABILITY, HALF_LIBRARY_DRAW_MECHANIC, half_library_draw_fraction, is_half_library_draw,
)
from ..replacement.immutable import thaw_value
from .affected_player_discard_capability_shapes import fixed_affected_player_discard_node_capabilities
from .affected_player_sacrifice_capability_shapes import fixed_affected_player_sacrifice_node_capabilities


def half_life_node_capabilities(*, effects, target_schema, mechanic_ids):
    mechanics = set(mechanic_ids)
    if 'scheduled-player-event-result' in mechanics:
        from .half_event_player_shapes import half_scheduled_player_node_capabilities
        return half_scheduled_player_node_capabilities(effects=effects,target_schema=target_schema,mechanic_ids=mechanic_ids)
    if 'fixed-event-player-result' in mechanics:
        from .half_event_player_shapes import half_event_player_node_capabilities
        return half_event_player_node_capabilities(effects=effects, target_schema=target_schema, mechanic_ids=mechanic_ids)
    if HALF_LIFE_MECHANIC not in mechanics or 'cr-119-life' not in mechanics or len(effects) != 1:
        return ()
    effect = effects[0]
    if effect.get('op') != HALF_LIFE_OPERATION:
        return ()
    try:
        HalfQuantitySpec.from_dict(effect['amount'])
    except (TypeError, ValueError, KeyError):
        return ()
    dependencies = {HALF_LIFE_CAPABILITY, 'life.change.effect'}
    if 'scope' in effect:
        return tuple(sorted(dependencies)) if (set(effect) == {'op', 'scope', 'amount'}
            and effect['scope'] in {'all_active_players', 'opponents'} and target_schema is None) else ()
    if set(effect) != {'op', 'player', 'amount'}:
        return ()
    player = effect['player']
    if player == '$target.0':
        if ('cr-115-targets' not in mechanics or dict(target_schema or {}) not in (
                {'zones': ['player'], 'categories': ['player'], 'player_relation': 'any', 'count': 1},
                {'zones': ['player'], 'categories': ['player'], 'player_relation': 'opponent', 'count': 1})):
            return ()
        dependencies.add('target.revalidate_resolution')
    elif target_schema is not None or player != '$controller':
        return ()
    return tuple(sorted(dependencies))


def half_choice_node_capabilities(*, effects, target_schema, mechanic_ids):
    if HALF_CHOICE_MECHANIC not in set(mechanic_ids) or len(effects) != 1:
        return ()
    effect = effects[0]
    try:
        HalfQuantitySpec.from_dict(effect['count'])
    except (TypeError, ValueError, KeyError):
        return ()
    # The existing fixed selector proves all player, zone and predicate fields.
    # Its count is replaced only for this proof; the emitted/runtime count stays typed.
    shell = {**effect, 'count': 1}
    validator = (fixed_affected_player_discard_node_capabilities if effect.get('then') == 'discard'
                 else fixed_affected_player_sacrifice_node_capabilities if effect.get('then') == 'sacrifice' else None)
    if validator is None:
        return ()
    dependencies = validator(effects=(shell,), target_schema=target_schema, mechanic_ids=mechanic_ids)
    return tuple(sorted({*dependencies, HALF_CHOICE_CAPABILITY})) if dependencies else ()


_HALF_DRAW_TARGET = {
    "zones": ["player"], "categories": ["player"], "player_relation": "any", "count": 1,
}


def half_library_draw_payload_dependencies(effect):
    if not is_half_library_draw(effect):
        return ()
    half_library_draw_fraction(effect)
    return (HALF_LIBRARY_DRAW_CAPABILITY, "target.revalidate_resolution", "zone.draw.library_to_hand")


def half_library_draw_node_capabilities(*, effects, target_schema, mechanic_ids):
    mechanics = set(mechanic_ids)
    if (len(effects) != 1 or thaw_value(target_schema) != _HALF_DRAW_TARGET
            or type(target_schema.get("count")) is not int
            or not {HALF_LIBRARY_DRAW_MECHANIC, "cr-121-drawing-a-card", "cr-115-targets"} <= mechanics):
        return ()
    try:
        return half_library_draw_payload_dependencies(effects[0])
    except (KeyError, TypeError, ValueError):
        return ()


def validate_half_library_draw_program_scope(program, nested):
    from ..compiler.target_player_sequences import TARGET_PLAYER_SEQUENCE_MECHANIC

    marked = (HALF_LIBRARY_DRAW_MECHANIC in program.coverage
              or HALF_LIBRARY_DRAW_CAPABILITY in program.capability_dependencies)
    matching = tuple(effect for effect in nested if is_half_library_draw(effect))
    if not marked and not matching:
        return
    effects = program.effects
    loss = {"op": HALF_LIFE_OPERATION, "player": "$target.0", "amount": HalfQuantitySpec("up").to_dict()}
    if (
        not marked or (program.event, program.active_zone) != ("resolve", "stack") or program.handlers
        or TARGET_PLAYER_SEQUENCE_MECHANIC not in program.coverage or len(effects) != 2
        or matching != (effects[0],) or thaw_value(effects[1]) != loss
        or HalfQuantitySpec.from_dict(effects[1]["amount"]).rounding != "up"
        or not half_library_draw_node_capabilities(
            effects=effects[:1], target_schema=program.target_schema, mechanic_ids=program.coverage,
        )
    ):
        raise ValueError("Half-library draw requires the original draw and same-target half-life spell")


def bind_half_library_draw(state, item, effect):
    if not is_half_library_draw(effect):
        return effect
    fraction = half_library_draw_fraction(effect)
    if (item.kind not in {"spell", "spell_copy"} or item.context.get("targets_revalidated") is not True
            or len(item.targets) != 1):
        raise ValueError("Half-library draw requires the revalidated original player target")
    player = state.players.get(item.targets[0])
    if player is None or not player.in_game:
        raise ValueError("Half-library draw lost its active recipient")
    return {**effect, "count": fraction.amount(len(player.zones["library"]))}


def standalone_fractional_result_capabilities(*, effects, target_schema, mechanics):
    """Route a standalone result while leaving composed leaves to their sequence owner."""
    from .closed_effect_program_shapes import CLOSED_EFFECT_PROGRAM_MECHANIC

    if not mechanics.isdisjoint({
        "fixed-nonrepeating-modal", "fixed-choose-one-modal-spell", CLOSED_EFFECT_PROGRAM_MECHANIC,
        HALF_LIBRARY_DRAW_MECHANIC,
    }):
        return None
    if "third-current-player-life-loss" in mechanics:
        from .third_life_shapes import third_life_node_capabilities

        return third_life_node_capabilities(effects=effects, target_schema=target_schema, mechanic_ids=mechanics)
    if HALF_LIFE_MECHANIC in mechanics:
        return half_life_node_capabilities(effects=effects, target_schema=target_schema, mechanic_ids=mechanics)
    if HALF_CHOICE_MECHANIC in mechanics:
        return half_choice_node_capabilities(effects=effects, target_schema=target_schema, mechanic_ids=mechanics)
    return None
