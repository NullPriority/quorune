"""Data-owned source authority for a repeated two-color upkeep price."""
from ..cumulative_alternative_mana_upkeep import CumulativeAlternativeManaSpec
from .cumulative_object_upkeep import CONTROLLER_UPKEEP


def cumulative_alternative_mana_upkeep_runtime_dependencies(program):
    required=set()
    for effect in program.effects:
        if effect.get('op')!='offer_optional_mana_payment' or effect.get('schema_version')!=9:continue
        if program.event!='step.begin' or program.active_zone!='battlefield' or program.target_schema is not None or program.event_condition!=CONTROLLER_UPKEEP:
            raise ValueError('Alternative upkeep lost its controller-upkeep source authority')
        required.update(CumulativeAlternativeManaSpec.from_effect(effect).capabilities)
    return tuple(sorted(required))
