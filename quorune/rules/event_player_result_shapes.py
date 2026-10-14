from __future__ import annotations

"""Capability closure for fixed untargeted event-addressed draw/life leaves."""

from ..compiler.event_player_results import EVENT_PLAYER_RESULT_CAPABILITY,EVENT_PLAYER_RESULT_MECHANIC
from .mill_capability_shapes import fixed_mill_node_capabilities,mill_covered_mechanics


def fixed_event_player_result_node_capabilities(*,effects,target_schema,mechanic_ids):
    if EVENT_PLAYER_RESULT_MECHANIC not in set(mechanic_ids) or target_schema is not None or len(effects)!=1:return ()
    effect=effects[0];layouts={'draw':('count',{'op','player','count','private'},'zone.draw.library_to_hand'),
        'life':('delta',{'op','player','delta'},'life.change.effect'),
        'lose_life':('amount',{'op','player','amount'},'life.change.effect')}
    if effect.get('op') not in layouts:return ()
    field,fields,capability=layouts[effect['op']]
    if (set(effect)!=fields or effect.get('player') not in {'$context.player','$context.target','$context.defending_player','$context.previous_controller'}
            or type(effect.get(field)) is not int or effect[field]<=0
            or effect['op']=='draw' and effect['private'] is not True):return ()
    return (EVENT_PLAYER_RESULT_CAPABILITY,capability)


def fixed_event_player_result_covered_mechanics(supplied):
    return ({EVENT_PLAYER_RESULT_MECHANIC} if EVENT_PLAYER_RESULT_CAPABILITY in supplied else set()) | mill_covered_mechanics(supplied)


def is_closed_fixed_event_player_result_program(program):
    required=fixed_event_player_result_node_capabilities(effects=program.effects,
        target_schema=program.target_schema,mechanic_ids=program.coverage)
    if not required or not set(required).issubset(program.capability_dependencies):return False
    player=program.effects[0]['player']
    if player=='$context.player':return program.event in {'spell.cast','card.drawn','player.scried','player.surveilled'}
    if player=='$context.previous_controller':return program.event=='card.discarded'
    from ..compiler.event_player_results import _damage_guarantees_player
    if player=='$context.target':return program.event in {'damage.dealt','damage.dealt.self'} and _damage_guarantees_player(program.event_condition)
    return program.event=='creature.attacks' and program.event_condition=={'field':'card','op':'eq','value':'$source.ref'}
