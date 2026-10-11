from __future__ import annotations

from ..enlist_model import ENLIST_CAPABILITY
from ..scalar_effect_amount_model import ScalarEffectAmountSpec,ScalarAmountOrigin


def enlist_reflexive_capabilities(*,effects,target_schema,mechanic_ids):
    expected={'op':'modify_stats_until_end_of_turn','card':'$source.zone_object','power':ScalarEffectAmountSpec(ScalarAmountOrigin.EVENT_OBJECT,characteristic='power').to_dict(),'toughness':0}
    return (ENLIST_CAPABILITY,) if 'enlist' in mechanic_ids and target_schema is None and len(effects)==1 and dict(effects[0])==expected else ()


def is_closed_enlist_reflexive_program(program):
    required=enlist_reflexive_capabilities(effects=program.effects,target_schema=program.target_schema,mechanic_ids=program.coverage)
    return bool(required) and set(required)<=set(program.capability_dependencies)
