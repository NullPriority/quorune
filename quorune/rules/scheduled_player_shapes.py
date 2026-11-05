from __future__ import annotations

"""Closed fixed effects addressed to the sealed scheduled active player."""

from typing import Any,Iterable,Mapping,Sequence
from ..event_player_result_values import event_result_value_capabilities
from .affected_player_discard_capability_shapes import (
    fixed_context_player_discard_node_capabilities,
    fixed_controller_discard_node_capabilities,
)

SCHEDULED_PLAYER_CAPABILITY = 'trigger.event.scheduled_player_result'
SCHEDULED_PLAYER_MECHANIC = 'scheduled-player-event-result'
SCHEDULED_PLAYER_DEPARTURE_COVERAGE = 'scheduled_player_departure_v1'


def scheduled_player_subject_instruction(effect: Mapping[str, Any]) -> bool:
    operation = effect.get('op')
    if operation == 'damage':
        return effect.get('target') == '$context.player'
    if operation == 'choose_cards_apnap':
        return effect.get('players') == ['$context.player']
    return (operation in ('life', 'lose_life', 'draw', 'mill')
            and effect.get('player') == '$context.player')


def scheduled_player_node_capabilities(*,effects:Sequence[Mapping[str,Any]],target_schema:Mapping[str,Any]|None,mechanic_ids:Iterable[str]):
    if SCHEDULED_PLAYER_MECHANIC not in set(mechanic_ids) or target_schema is not None or not 1<=len(effects)<=2:return ()
    if not any(scheduled_player_subject_instruction(effect) for effect in effects):return ()
    capabilities={SCHEDULED_PLAYER_CAPABILITY}
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


def _scheduled_steps(condition: Mapping[str, Any] | None) -> set[str]:
    if not isinstance(condition, Mapping):
        return set()
    if set(condition) == {'all'} and isinstance(condition['all'], (list, tuple)):
        return set().union(*(_scheduled_steps(child) for child in condition['all']))
    value = condition.get('value')
    if type(value) is str and condition == {'field': 'step', 'op': 'eq', 'value': value}:
        return {value}
    return set()


def scheduled_player_result_step(program) -> str | None:
    steps = _scheduled_steps(program.event_condition)
    if program.event == 'step.begin' and steps in ({'upkeep'}, {'draw'}):
        return next(iter(steps))
    return None


def scheduled_player_departure_runtime_dependencies(program) -> tuple[str, ...]:
    if SCHEDULED_PLAYER_DEPARTURE_COVERAGE not in program.coverage:
        return ()
    if scheduled_player_result_step(program) is None or not is_closed_scheduled_player_program(program):
        raise ValueError('Scheduled player departure requires a closed upkeep or draw result')
    return (SCHEDULED_PLAYER_CAPABILITY,)
