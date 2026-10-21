from __future__ import annotations

"""Closed producer for one mandatory unrestricted random hand payment."""

from dataclasses import dataclass,replace
import re
from ..rules.random_discard_additional_cost import FixedRandomDiscardAdditionalCost,random_spell_cost_dependencies


@dataclass(frozen=True,slots=True)
class FixedRandomDiscardSpellCostTemplate:
    template_id: str = 'spell-additional-cost-fixed-random-discard-v1'

    @property
    def cost_schema(self):return {'additional_costs':[FixedRandomDiscardAdditionalCost().to_descriptor()]}


def fixed_random_discard_spell_cost_template(text):
    if re.fullmatch(r'As an additional cost to cast this spell, discard a card at random\.?',text.strip(),re.I):
        return FixedRandomDiscardSpellCostTemplate()
    return None


def closed_random_discard_spell_result(program,*,recognizers):
    """Keep the typed price and independently closed result as two obligations."""
    try:required=random_spell_cost_dependencies(program.cost_schema)
    except (TypeError,ValueError):return False
    if not required or not set(required)<=set(program.capability_dependencies):return False
    prefix='spell-additional-cost-fixed-random-discard-v1+'
    template=str(program.provenance.get('template_id') or '')
    if not template.startswith(prefix):return False
    result=replace(program,cost_schema=None,provenance={**program.provenance,'template_id':template[len(prefix):]},
        coverage=[value for value in program.coverage if value!='cr-601-casting-spells'])
    return any(recognizer(result) for recognizer in recognizers)
