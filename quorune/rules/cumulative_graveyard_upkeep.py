"""Data-owned original upkeep authority for grouped graveyard costs."""

from ..cumulative_graveyard_upkeep import GraveyardLibraryCostSpec, WRAPPER_VERSION
from .cumulative_object_upkeep import CONTROLLER_UPKEEP


def cumulative_graveyard_upkeep_runtime_dependencies(program):
    required = set()
    for effect in program.effects:
        if effect.get('op') != 'offer_optional_mana_payment' or effect.get('schema_version') != WRAPPER_VERSION:continue
        if (program.event != 'step.begin' or program.active_zone != 'battlefield' or program.target_schema is not None
                or program.event_condition != CONTROLLER_UPKEEP or program.cost_schema is not None):
            raise ValueError('Grouped graveyard cost requires its original controller-upkeep occurrence')
        required.update(GraveyardLibraryCostSpec.from_effect(effect).capabilities)
    return tuple(sorted(required))
