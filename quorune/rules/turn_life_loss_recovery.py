"""Read existing authoritative turn facts; never infer loss from life totals."""
from collections import Counter
from collections.abc import Mapping
from ..turn_history import current_turn_history_events
from ..turn_life_loss_amount_model import (AllPlayerTurnLifeLossSpec,
    TURN_LIFE_LOSS_TOTAL_KIND as KIND,TURN_LIFE_LOSS_TOTAL_CAPABILITY as CAP)
from ..query_effect_amount_model import PublicQueryAmountError


def _actual_current_turn_life_loss_events(state):
    history = state.turn_history
    sequence = state.turn_sequence
    if history is None or type(history.schema_version) is not int or history.schema_version!=1 or history.turn_sequence!=sequence:
        raise PublicQueryAmountError('Current-turn life-loss history is unavailable')
    events=current_turn_history_events(history,turn_sequence=sequence,kind='player_lost_life')
    if any(type(event.amount) is not int or event.amount<0 for event in events):
        raise PublicQueryAmountError('Actual life-loss history amounts must be nonnegative integers')
    return events


def resolve_all_player_turn_life_loss(host,value,item):
    AllPlayerTurnLifeLossSpec.from_dict(value)
    return sum(event.amount for event in _actual_current_turn_life_loss_events(host.state))


def current_turn_life_loss_by_player(state):
    amounts = Counter()
    for event in _actual_current_turn_life_loss_events(state):
        if type(event.target) is not str or not event.target:
            raise PublicQueryAmountError('Actual life loss requires the affected player identity')
        amounts[event.target] += event.amount
    return amounts


def turn_life_loss_value_dependencies(value):
    if not isinstance(value,Mapping) or value.get('kind')!=KIND:return ()
    AllPlayerTurnLifeLossSpec.from_dict(value)
    return (CAP,'quantity_expression.scalar_effect_amount')


def turn_life_loss_node_capabilities(*,effects,target_schema,mechanic_ids=(),cost_schema=None,**kwargs):
    if len(effects)!=1 or target_schema is not None:return ()
    effect=effects[0]
    if (set(effect)!={'op','player','delta'} or effect['op']!='life' or effect['player']!='$controller'
            or not isinstance(effect['delta'],Mapping) or effect['delta'].get('kind')!=KIND):return ()
    try:AllPlayerTurnLifeLossSpec.from_dict(effect['delta'])
    except (ValueError,TypeError):return ()
    from ..scalar_effect_amount_model import ScalarEffectAmountSpec,ScalarAmountOrigin,SCALAR_AMOUNT_MECHANIC
    from .capabilities import capability_dependencies_for_node
    scalar=ScalarEffectAmountSpec(ScalarAmountOrigin.HISTORY,history_fact='life_lost')
    base=capability_dependencies_for_node(effects=({**effect,'delta':scalar.to_dict()},),target_schema=None,
        mechanic_ids=(SCALAR_AMOUNT_MECHANIC,'cr-119-life'),cost_schema=cost_schema)
    return tuple(sorted({CAP,*base})) if base else ()


def _has_total(value):
    if isinstance(value,Mapping):return value.get('kind')==KIND or any(_has_total(v) for v in value.values())
    return isinstance(value,(list,tuple)) and any(_has_total(v) for v in value)


def turn_life_loss_program_dependencies(program):
    if not _has_total((program.effects,program.handlers,program.target_schema,program.cost_schema)):return ()
    if (program.target_schema is not None or _has_total(program.cost_schema)
            or not ((program.event=='resolve' and program.active_zone=='stack' and str(program.ability_id).startswith('spell:'))
                or (program.event=='activate' and program.active_zone=='battlefield'))):
        raise ValueError('All-player turn loss requires its ordinary untargeted resolution frame')
    required=turn_life_loss_node_capabilities(effects=program.effects,target_schema=program.target_schema,
        mechanic_ids=program.coverage,cost_schema=program.cost_schema)
    if not required:raise ValueError('All-player turn loss requires one complete controller gain instruction')
    return required


def is_closed_turn_life_loss_program(program):
    try:required=turn_life_loss_program_dependencies(program)
    except (ValueError,TypeError):return False
    return bool(required) and set(required)<=set(program.capability_dependencies)
