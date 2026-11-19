"""Payload-owned original upkeep/source and beneficial-cost requirements."""
from ..cumulative_effect_cost_upkeep import UpkeepEffectCostSpec
from .cumulative_object_upkeep import CONTROLLER_UPKEEP


def cumulative_effect_cost_upkeep_runtime_dependencies(program):
    required=set()
    for effect in program.effects:
        if effect.get('op')!='offer_optional_mana_payment' or effect.get('schema_version')!=10:continue
        if program.event!='step.begin' or program.active_zone!='battlefield' or program.target_schema is not None or program.event_condition!=CONTROLLER_UPKEEP:
            raise ValueError('Beneficial upkeep cost lost its original controller-upkeep source authority')
        required.update(UpkeepEffectCostSpec.from_effect(effect).capabilities)
    return tuple(sorted(required))
