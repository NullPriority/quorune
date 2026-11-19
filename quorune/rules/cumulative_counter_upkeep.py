"""Payload-owned counter upkeep scope and cost requirements."""
from ..cumulative_counter_upkeep import CumulativeCounterUpkeepSpec
from .cumulative_object_upkeep import CONTROLLER_UPKEEP


def cumulative_counter_upkeep_runtime_dependencies(program):
    required=set()
    for effect in program.effects:
        if effect.get('op')!='offer_optional_mana_payment' or effect.get('schema_version')!=8:continue
        if program.event!='step.begin' or program.active_zone!='battlefield' or program.target_schema is not None or program.event_condition!=CONTROLLER_UPKEEP:
            raise ValueError('Counter upkeep requires its original controller-upkeep occurrence')
        required.update(CumulativeCounterUpkeepSpec.from_effect(effect).capabilities)
    return tuple(sorted(required))
