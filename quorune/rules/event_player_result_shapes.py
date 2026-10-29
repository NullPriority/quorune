from __future__ import annotations

"""Capability closure for fixed untargeted event-addressed draw/life leaves."""

from ..compiler.event_player_results import EVENT_PLAYER_RESULT_CAPABILITY,EVENT_PLAYER_RESULT_MECHANIC
from .mill_capability_shapes import fixed_mill_node_capabilities,mill_covered_mechanics
from ..event_player_result_values import event_result_value_capabilities
from ..fractional_quantity_model import HALF_LIFE_CAPABILITY,HALF_LIFE_MECHANIC


def fixed_event_player_result_node_capabilities(*,effects,target_schema,mechanic_ids):
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
    return ({EVENT_PLAYER_RESULT_MECHANIC} if EVENT_PLAYER_RESULT_CAPABILITY in supplied else set()) | mill_covered_mechanics(supplied)


def is_closed_fixed_event_player_result_program(program):
    required=fixed_event_player_result_node_capabilities(effects=program.effects,
        target_schema=program.target_schema,mechanic_ids=program.coverage)
    if not required or not set(required).issubset(program.capability_dependencies):return False
    return all(_event_player_reference_is_closed(program,effect['player']) for effect in program.effects)


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
