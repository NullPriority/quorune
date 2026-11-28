"""Closed third-life result authority independent of coverage declarations."""
from collections.abc import Mapping
from ..third_quantity_model import ThirdQuantitySpec,THIRD_QUANTITY_KIND,THIRD_LIFE_CAPABILITY,THIRD_LIFE_MECHANIC


def third_life_effect_dependencies(effect):
    if not isinstance(effect,Mapping):return ()
    amount=effect.get('amount')
    if not isinstance(amount,Mapping) or amount.get('kind')!=THIRD_QUANTITY_KIND:return ()
    fraction=ThirdQuantitySpec.from_dict(amount)
    if (set(effect)!={'op','scope','amount'} or effect['op']!='lose_life'
            or effect['scope']!='all_active_players' or fraction.rounding!='up'):
        raise ValueError('Third-life loss accepts one complete all-active-player rounded-up result')
    return (THIRD_LIFE_CAPABILITY,'life.change.effect')


def third_life_node_capabilities(*,effects,target_schema,mechanic_ids):
    if (THIRD_LIFE_MECHANIC not in set(mechanic_ids) or 'cr-119-life' not in set(mechanic_ids)
            or target_schema is not None or len(effects)!=1):return ()
    try:return third_life_effect_dependencies(effects[0])
    except (TypeError,ValueError,KeyError):return ()


def third_life_program_dependencies(program):
    from .third_player_rounds import third_player_rounds_program_dependencies

    if owned := third_player_rounds_program_dependencies(program):
        return owned

    def contains(value):
        if isinstance(value,Mapping):
            return value.get('kind')==THIRD_QUANTITY_KIND or any(contains(v) for v in value.values())
        return isinstance(value,(list,tuple)) and any(contains(v) for v in value)
    if not contains(program.effects):return ()
    zones={'resolve':'stack','activate':'battlefield','permanent.enter.self':'battlefield'}
    if (len(program.effects)!=1 or program.target_schema is not None
            or zones.get(program.event)!=program.active_zone):
        raise ValueError('Third-life result requires its complete independently owned frame')
    required=third_life_effect_dependencies(program.effects[0])
    if not required:raise ValueError('Third-life quantity is outside its producer result')
    return required
