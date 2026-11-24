"""Fail-closed compiler ownership for the represented discard-result draws."""
from ..discard_result_model import (
    DAMAGE_RECIPIENT_REFERENCE, DISCARD_RESULT_CAPABILITY, DISCARD_RESULT_EVENT_PLAYER_REFERENCES,
    DISCARD_RESULT_MECHANIC, DISCARD_RESULT_OPERATION, EVENT_PLAYER_DISCARD_RESULT_CAPABILITY,
    EVENT_PLAYER_DISCARD_RESULT_VERSION, SELECTED_LAND_DISCARD_RESULT_VERSION, SELECTED_LAND_DISCARD_FIELD, validate_discard_draw,
)
from ..event_player_modifier_model import DEFENDING_PLAYER_REFERENCE
from .effect_shape_walkers import nested_effects
from .graveyard_arrival_order import GRAVEYARD_ARRIVAL_ORDER_CAPABILITY
from ..discard_result_model import FIXED_SUBSET_DISCARD_DRAW_CAPABILITY, FIXED_SUBSET_DISCARD_DRAW_MECHANIC
from ..discard_result_model import validate_fixed_subset_discard_draw
from ..discard_result_model import VARIABLE_SUBSET_DISCARD_DRAW_CAPABILITY, VARIABLE_SUBSET_DISCARD_DRAW_MECHANIC, validate_variable_subset_discard_draw


def discard_result_node_capabilities(*, effects, target_schema, mechanic_ids, **kwargs):
    if VARIABLE_SUBSET_DISCARD_DRAW_MECHANIC in mechanic_ids and target_schema is None and len(effects)==1:
        try:validate_variable_subset_discard_draw(effects[0])
        except (TypeError,ValueError):return ()
        return (VARIABLE_SUBSET_DISCARD_DRAW_CAPABILITY,)
    if FIXED_SUBSET_DISCARD_DRAW_MECHANIC in mechanic_ids and target_schema is None and len(effects)==1:
        try:validate_fixed_subset_discard_draw(effects[0])
        except (TypeError,ValueError):return ()
        return (FIXED_SUBSET_DISCARD_DRAW_CAPABILITY,)
    if DISCARD_RESULT_MECHANIC not in mechanic_ids or target_schema is not None or len(effects) != 1:return ()
    try:validate_discard_draw(effects[0])
    except (ValueError, TypeError):return ()
    effect = effects[0]
    if effect['actor'] != '$controller':return ()
    if effect['schema_version'] == SELECTED_LAND_DISCARD_RESULT_VERSION:
        if SELECTED_LAND_DISCARD_FIELD in effect:
            return ()
        return (DISCARD_RESULT_CAPABILITY, GRAVEYARD_ARRIVAL_ORDER_CAPABILITY, VARIABLE_SUBSET_DISCARD_DRAW_CAPABILITY,
                'counter.producer.fixed_effect', 'zone.draw.library_to_hand')
    if effect['schema_version'] == EVENT_PLAYER_DISCARD_RESULT_VERSION:
        if tuple(effect['players']) in ((DEFENDING_PLAYER_REFERENCE,), (DAMAGE_RECIPIENT_REFERENCE,)):
            return (DISCARD_RESULT_CAPABILITY, EVENT_PLAYER_DISCARD_RESULT_CAPABILITY, GRAVEYARD_ARRIVAL_ORDER_CAPABILITY)
        return ()
    if effect['players'] not in ('all', ['$controller']):return ()
    if effect['reduction'] == 'greatest' and effect['players'] != 'all':return ()
    return (DISCARD_RESULT_CAPABILITY,)


def _uses_event_discard_result(effect):
    if effect.get('op') != DISCARD_RESULT_OPERATION:
        return False
    players = effect.get('players')
    return (effect.get('schema_version') == EVENT_PLAYER_DISCARD_RESULT_VERSION
            or isinstance(players, (list, tuple))
            and any(isinstance(player, str) and player in DISCARD_RESULT_EVENT_PLAYER_REFERENCES for player in players))


def event_player_discard_result_runtime_dependencies(program):
    from .affected_player_discard_capability_shapes import equipped_damage_discard_untap_runtime_dependencies

    if equipped := equipped_damage_discard_untap_runtime_dependencies(program):
        return equipped
    nodes = tuple(nested_effects((program.effects, getattr(program, 'handlers', ()))))
    if any(effect.get('op') == DISCARD_RESULT_OPERATION
           and effect.get('schema_version') == SELECTED_LAND_DISCARD_RESULT_VERSION for effect in nodes):
        required = discard_result_node_capabilities(effects=program.effects,
            target_schema=program.target_schema, mechanic_ids=program.coverage)
        if (not required or (program.event, program.active_zone) != ('permanent.enter.self', 'battlefield')
                or program.event_condition is not None or program.cost_schema is not None or program.handlers):
            raise ValueError('Selected land discard requires the original untargeted entry resolution')
        return (*required, 'trigger.event.normalized_zone_change', 'trigger.placement.apnap')
    if not any(_uses_event_discard_result(effect) for effect in nodes):
        return ()
    from ..compiler.fixed_public_event_trigger_bindings import source_damage_incarnation_condition

    blocked_condition = {'field': 'card', 'op': 'eq', 'value': '$source.ref'}
    damage_condition = {'all': [
        {'field': 'target_kind', 'op': 'eq', 'value': 'player'}, source_damage_incarnation_condition(),
    ]}
    if program.event == 'creature.becomes_blocked':
        condition, player, trigger = blocked_condition, DEFENDING_PLAYER_REFERENCE, 'trigger.event.normalized_public_action'
    elif program.event == 'damage.dealt.self':
        condition, player, trigger = damage_condition, DAMAGE_RECIPIENT_REFERENCE, 'trigger.event.normalized_damage'
    else:
        raise ValueError('Event-player discard result has no matching source event')
    required = discard_result_node_capabilities(
        effects=program.effects, target_schema=program.target_schema, mechanic_ids=(DISCARD_RESULT_MECHANIC,),
    )
    if (EVENT_PLAYER_DISCARD_RESULT_CAPABILITY not in required or program.active_zone != 'battlefield'
            or program.event_condition != condition or tuple(program.effects[0]['players']) != (player,)
            or getattr(program, 'cost_schema', None) is not None or getattr(program, 'handlers', ())):
        raise ValueError('Event-player discard result requires one exact untargeted occurrence and current hand')
    return (*required, trigger, 'trigger.placement.apnap')


def is_closed_discard_result_program(program):
    deps = discard_result_node_capabilities(effects=program.effects, target_schema=program.target_schema, mechanic_ids=program.coverage)
    return bool(deps) and set(deps) <= set(program.capability_dependencies)
