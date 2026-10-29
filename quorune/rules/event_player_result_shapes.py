from __future__ import annotations

"""Capability closure for fixed untargeted event-addressed draw/life leaves."""

from ..compiler.event_player_results import (EVENT_PLAYER_RESULT_CAPABILITY,EVENT_PLAYER_RESULT_MECHANIC,
    EVENT_PLAYER_DAMAGE_CAPABILITY,EVENT_PLAYER_DAMAGE_MECHANIC)
from .mill_capability_shapes import fixed_mill_node_capabilities,mill_covered_mechanics
from ..event_player_result_values import event_result_value_capabilities
from ..fractional_quantity_model import HALF_LIFE_CAPABILITY,HALF_LIFE_MECHANIC


def fixed_event_player_result_node_capabilities(*,effects,target_schema,mechanic_ids):
    if EVENT_PLAYER_DAMAGE_MECHANIC in set(mechanic_ids):
        return fixed_event_player_damage_node_capabilities(effects=effects,target_schema=target_schema,mechanic_ids=mechanic_ids)
    if EVENT_PLAYER_RESULT_MECHANIC not in set(mechanic_ids) or target_schema is not None or not 1<=len(effects)<=2:return ()
    if not any(effect.get('player')!='$controller' for effect in effects):return ()
    layouts={'draw':('count',{'op','player','count','private'},'zone.draw.library_to_hand'),
        'life':('delta',{'op','player','delta'},'life.change.effect'),
        'lose_life':('amount',{'op','player','amount'},'life.change.effect')}
    capabilities={EVENT_PLAYER_RESULT_CAPABILITY}
    for effect in effects:
        if effect.get('op') not in layouts:return ()
        field,fields,capability=layouts[effect['op']]
        extra=event_result_value_capabilities(effect['op'],field,effect.get(field))
        if (set(effect)!=fields or effect.get('player') not in {'$controller','$context.player','$context.target','$context.defending_player','$context.previous_controller','$context.controller'}
                or extra is None or HALF_LIFE_CAPABILITY in extra and HALF_LIFE_MECHANIC not in set(mechanic_ids)
                or effect['op']=='draw' and effect['private'] is not True):return ()
        capabilities.update((capability,*extra))
    return tuple(sorted(capabilities))


def fixed_event_player_result_covered_mechanics(supplied):
    return ({EVENT_PLAYER_RESULT_MECHANIC} if EVENT_PLAYER_RESULT_CAPABILITY in supplied else set()) | mill_covered_mechanics(supplied) | (
        {EVENT_PLAYER_DAMAGE_MECHANIC} if EVENT_PLAYER_DAMAGE_CAPABILITY in supplied else set())


def is_closed_fixed_event_player_result_program(program):
    required=fixed_event_player_result_node_capabilities(effects=program.effects,
        target_schema=program.target_schema,mechanic_ids=program.coverage)
    if not required or not set(required).issubset(program.capability_dependencies):return False
    return all(_event_player_reference_is_closed(program,effect['target'] if effect.get('op')=='damage' else effect['player']) for effect in program.effects)


def fixed_event_player_damage_node_capabilities(*,effects,target_schema,mechanic_ids):
    if not {EVENT_PLAYER_RESULT_MECHANIC,EVENT_PLAYER_DAMAGE_MECHANIC}<=set(mechanic_ids) or target_schema is not None or len(effects)!=1:return ()
    effect=effects[0]
    if (set(effect)!={'op','source','target','amount'} or effect.get('op')!='damage' or effect.get('source')!='$source'
            or effect.get('target') not in {'$context.player','$context.target','$context.defending_player','$context.previous_controller','$context.controller'}
            or event_result_value_capabilities('damage','amount',effect.get('amount')) is None):return ()
    from .damage_capability_shapes import fixed_damage_node_capabilities
    extra=event_result_value_capabilities('damage','amount',effect['amount'])
    amount=effect['amount'] if type(effect['amount']) is int else 2
    damage=fixed_damage_node_capabilities(effects=({**effect,'target':'$controller','amount':amount},),target_schema=None,mechanic_ids={'cr-120-damage'})
    return tuple(sorted({EVENT_PLAYER_RESULT_CAPABILITY,EVENT_PLAYER_DAMAGE_CAPABILITY,*damage,*extra})) if damage else ()


def event_player_damage_runtime_dependencies(program):
    from collections.abc import Mapping
    required=set();has_marker=EVENT_PLAYER_DAMAGE_MECHANIC in program.coverage
    def visit(node):
        if isinstance(node,Mapping):
            if (node.get('op')=='damage' and node.get('source')=='$source' and event_result_value_capabilities('damage','amount',node.get('amount')) is not None
                    and _event_player_reference_is_closed(program,node.get('target')) and node.get('target')!='$controller'):
                caps=fixed_event_player_damage_node_capabilities(effects=(node,),target_schema=None,
                    mechanic_ids={EVENT_PLAYER_RESULT_MECHANIC,EVENT_PLAYER_DAMAGE_MECHANIC})
                if not caps:raise ValueError('Malformed fixed event-player damage instruction')
                required.update(caps)
            for child in node.values():visit(child)
        elif isinstance(node,(list,tuple)):
            for child in node:visit(child)
    visit(program.effects)
    if has_marker and not required:raise ValueError('Fixed event-player damage marker lacks a closed bound instruction')
    return tuple(sorted(required))


def _event_player_reference_is_closed(program,player):
    if player=='$controller':return True
    from .event_subscriptions import FixedEventSubscriptionSet
    try:
        subscriptions=FixedEventSubscriptionSet.from_condition(program.event_condition)
    except (TypeError,ValueError):return False
    occurrences=((program.event,program.event_condition),) if subscriptions is None else tuple(
        (entry.event,entry.condition) for entry in subscriptions.subscriptions)
    if program.event not in {event for event,_condition in occurrences}:return False
    return all(_occurrence_player_reference_is_closed(event,condition,program.coverage,player)
        for event,condition in occurrences)


def _occurrence_player_reference_is_closed(event,condition,coverage,player):
    if player=='$context.player':return event in {'spell.cast','card.drawn','player.scried','player.surveilled'}
    if player=='$context.previous_controller':return event in {'card.discarded','creature.dies','creature.dies.self'}
    if player=='$context.controller':return event in {'land.enter','creature.enter','permanent.enter','artifact.enter','enchantment.enter'}
    from ..compiler.event_player_results import _damage_guarantees_player
    if player=='$context.target':return event in {'damage.dealt','damage.dealt.self'} and _damage_guarantees_player(condition)
    return (player=='$context.defending_player' and event=='creature.attacks'
        and bool({'trigger-event-normalized-public-action','trigger-event-normalized-self-attack'} & set(coverage))
        and 'one_or_more_event_batch' not in coverage)
