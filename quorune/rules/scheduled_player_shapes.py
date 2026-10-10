from __future__ import annotations

"""Closed fixed effects addressed to the sealed scheduled active player."""

from typing import Any,Iterable,Mapping,Sequence


def scheduled_player_node_capabilities(*,effects:Sequence[Mapping[str,Any]],target_schema:Mapping[str,Any]|None,mechanic_ids:Iterable[str]):
    if 'scheduled-player-event-result' not in set(mechanic_ids) or target_schema is not None or not 1<=len(effects)<=2:return ()
    capabilities={'trigger.event.scheduled_player_result'}
    for effect in effects:
        op=effect.get('op')
        layouts={'life':('delta',{'op','player','delta'},'life.change.effect'),
            'lose_life':('amount',{'op','player','amount'},'life.change.effect'),
            'draw':('count',{'op','player','count','private'},'zone.draw.library_to_hand'),
            'mill':('count',{'op','player','count'},'zone.mill.fixed'),
            'damage':('amount',{'op','source','target','amount'},'damage.amount.positive')}
        if op not in layouts:return ()
        field,fields,capability=layouts[op]
        reference='target' if op=='damage' else 'player'
        if set(effect)!=fields or effect.get(reference)!='$context.player' or type(effect.get(field)) is not int:
            return ()
        if (effect[field]==0 if op=='life' else effect[field]<=0):return ()
        if op=='draw' and effect['private'] is not True:return ()
        if op=='damage':
            if effect['source']!='$source':return ()
            capabilities.add('damage.result.player_life')
        capabilities.add(capability)
    return tuple(sorted(capabilities))


def is_closed_scheduled_player_program(program):
    required=scheduled_player_node_capabilities(effects=program.effects,target_schema=program.target_schema,mechanic_ids=program.coverage)
    return bool(required) and set(required).issubset(program.capability_dependencies)
