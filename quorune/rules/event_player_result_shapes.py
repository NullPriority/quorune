from __future__ import annotations

"""Capability closure for fixed untargeted event-addressed draw/life leaves."""

from ..compiler.event_player_results import EVENT_PLAYER_RESULT_CAPABILITY,EVENT_PLAYER_RESULT_MECHANIC
from .mill_capability_shapes import fixed_mill_node_capabilities,mill_covered_mechanics


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
        if (set(effect)!=fields or effect.get('player') not in {'$controller','$context.player','$context.target','$context.defending_player','$context.previous_controller','$context.controller'}
                or type(effect.get(field)) is not int or effect[field]<=0
                or effect['op']=='draw' and effect['private'] is not True):return ()
        capabilities.add(capability)
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
    if player=='$context.player':return program.event in {'spell.cast','card.drawn','player.scried','player.surveilled'}
    if player=='$context.previous_controller':return program.event in {'card.discarded','creature.dies'}
    if player=='$context.controller':return program.event in {'land.enter','creature.enter','permanent.enter','artifact.enter','enchantment.enter'}
    from ..compiler.event_player_results import _damage_guarantees_player
    if player=='$context.target':return program.event in {'damage.dealt','damage.dealt.self'} and _damage_guarantees_player(program.event_condition)
    return program.event=='creature.attacks' and 'trigger-event-normalized-public-action' in program.coverage and 'one_or_more_event_batch' not in program.coverage
