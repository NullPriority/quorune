from __future__ import annotations

"""Source compiler for one mandatory fixed Waterbend casting cost."""

from dataclasses import dataclass,replace
import re
from ..waterbend import FixedWaterbendCost,WATERBEND_CAPABILITY,WATERBEND_COMPOSITION_CAPABILITY,WATERBEND_COMPOSITION_MECHANIC


@dataclass(frozen=True,slots=True)
class FixedWaterbendCostTemplate:
    cost: FixedWaterbendCost

    @property
    def template_id(self):return 'spell-additional-cost-fixed-waterbend-v1'

    @property
    def cost_schema(self):return {'additional_costs':[self.cost.to_dict()]}


def fixed_waterbend_additional_cost_template(text):
    match=re.fullmatch(r'As an additional cost to cast this spell, waterbend \{(?P<amount>\d+)\}\.?',text.strip(),re.I)
    return FixedWaterbendCostTemplate(FixedWaterbendCost(int(match['amount']))) if match is not None else None


def closed_waterbend_source_result(ability,compiled):
    """Use the existing incarnation-pinned source pump template for this cost."""
    template,effects,schema,mechanics=compiled
    if ability.waterbend is not None and len(effects)==1 and effects[0].get('op')=='modify_stats_until_end_of_turn' and effects[0].get('card')=='$source':
        from .fixed_target_effect_sequences import FixedSourceCharacteristicsTemplate
        effect=effects[0]
        if type(effect.get('power')) is int and type(effect.get('toughness')) is int:
            return FixedSourceCharacteristicsTemplate(source_kind='permanent',power=effect['power'],toughness=effect['toughness']).compiled()
    return compiled


def closed_waterbend_spell_result(program,*,recognizers):
    """Validate the complete typed price, then its independently owned result."""
    schema=program.cost_schema
    if not isinstance(schema,dict) or set(schema)!={'additional_costs'} or type(schema['additional_costs']) is not list or len(schema['additional_costs'])!=1:return False
    try:FixedWaterbendCost.from_dict(schema['additional_costs'][0])
    except (TypeError,ValueError):return False
    prefix='spell-additional-cost-fixed-waterbend-v1+'
    template=str(program.provenance.get('template_id') or '')
    if not template.startswith(prefix) or not {WATERBEND_CAPABILITY,WATERBEND_COMPOSITION_CAPABILITY}<=set(program.capability_dependencies):return False
    result=replace(program,cost_schema=None,provenance={**program.provenance,'template_id':template[len(prefix):]},
        coverage=[value for value in program.coverage if value not in {'cr-601-casting-spells',WATERBEND_COMPOSITION_MECHANIC}])
    return any(recognizer(result) for recognizer in recognizers)
