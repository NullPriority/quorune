from __future__ import annotations

"""Data-derived ownership and occurrence authority for repeated upkeep costs."""
from ..cumulative_object_upkeep import CumulativeObjectUpkeepSpec,WRAPPER_VERSION

CONTROLLER_UPKEEP={'all':[{'field':'player','op':'eq','value':'$source.controller'},{'field':'step','op':'eq','value':'upkeep'}]}


def cumulative_object_upkeep_runtime_dependencies(program):
    required=set()
    def visit(effects):
        for effect in effects:
            if effect.get('op')=='offer_optional_mana_payment' and effect.get('schema_version')==WRAPPER_VERSION:
                if program.event!='step.begin' or program.active_zone!='battlefield' or program.target_schema is not None or program.event_condition!=CONTROLLER_UPKEEP:
                    raise ValueError('Object cumulative upkeep lost controller-upkeep source authority')
                required.update(CumulativeObjectUpkeepSpec.from_effect(effect).capabilities)
            for key in ('effects','declined_effects'):
                if isinstance(effect.get(key),(list,tuple)):visit(effect[key])
    visit(program.effects)
    return tuple(sorted(required))
