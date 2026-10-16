from __future__ import annotations

"""Prove the half quantity separately from the existing player/selector shell."""

from collections.abc import Mapping

from ..fractional_quantity_model import (
    HalfQuantitySpec, HALF_LIFE_OPERATION, HALF_LIFE_MECHANIC, HALF_LIFE_CAPABILITY,
    HALF_CHOICE_MECHANIC, HALF_CHOICE_CAPABILITY,
)
from .affected_player_discard_capability_shapes import fixed_affected_player_discard_node_capabilities
from .affected_player_sacrifice_capability_shapes import fixed_affected_player_sacrifice_node_capabilities


def half_life_node_capabilities(*, effects, target_schema, mechanic_ids):
    mechanics = set(mechanic_ids)
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
