from __future__ import annotations

"""Closed fixed effects addressed to the sealed scheduled active player."""

from typing import Any,Iterable,Mapping,Sequence
from ..event_player_result_values import event_result_value_capabilities
from .affected_player_discard_capability_shapes import (
    fixed_context_player_discard_node_capabilities,
    fixed_controller_discard_node_capabilities,
)


def scheduled_player_node_capabilities(*,effects:Sequence[Mapping[str,Any]],target_schema:Mapping[str,Any]|None,mechanic_ids:Iterable[str]):
    if 'scheduled-player-event-result' not in set(mechanic_ids) or target_schema is not None or not 1<=len(effects)<=2:return ()
    if not any(effect.get('player', effect.get('target')) == '$context.player' or effect.get('players') == ['$context.player'] for effect in effects):return ()
    capabilities={'trigger.event.scheduled_player_result'}
    for effect in effects:
        op=effect.get('op')
        if op == 'choose_cards_apnap':
            if effect.get('players') not in (['$context.player'], ['$controller']):
                return ()
            checker = fixed_context_player_discard_node_capabilities if effect.get('players') == ['$context.player'] else fixed_controller_discard_node_capabilities
            required = checker(effects=(effect,), target_schema=None, mechanic_ids=mechanic_ids)
            if not required:
                return ()
            capabilities.update(required)
            continue
        layouts={'life':('delta',{'op','player','delta'},'life.change.effect'),
            'lose_life':('amount',{'op','player','amount'},'life.change.effect'),
            'draw':('count',{'op','player','count','private'},'zone.draw.library_to_hand'),
            'mill':('count',{'op','player','count'},'zone.mill.fixed'),
            'damage':('amount',{'op','source','target','amount'},'damage.amount.positive')}
        if op not in layouts:return ()
        field,fields,capability=layouts[op]
        reference='target' if op=='damage' else 'player'
        players = {'$context.player'} if op == 'damage' else {'$context.player', '$controller'}
        if set(effect)!=fields or type(effect.get(reference)) is not str or effect.get(reference) not in players:
            return ()
        if op == 'lose_life':
            value_capabilities=event_result_value_capabilities(op,field,effect.get(field))
            if value_capabilities is None:return ()
            if value_capabilities and 'half-current-player-life-loss' not in set(mechanic_ids):return ()
            capabilities.update(value_capabilities)
        elif type(effect.get(field)) is not int or (effect[field]==0 if op=='life' else effect[field]<=0):return ()
        if op=='draw' and effect['private'] is not True:return ()
        if op=='damage':
            if effect['source']!='$source':return ()
            capabilities.add('damage.result.player_life')
        capabilities.add(capability)
    return tuple(sorted(capabilities))


def is_closed_scheduled_player_program(program):
    required=scheduled_player_node_capabilities(effects=program.effects,target_schema=program.target_schema,mechanic_ids=program.coverage)
    return bool(required) and set(required).issubset(program.capability_dependencies)
