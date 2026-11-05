from __future__ import annotations

"""Keep the canonical fixed sacrifice price and its closed result separate."""

from dataclasses import replace
from collections.abc import Mapping
from ..rules.casting_additional_costs import fixed_sacrifice_additional_cost
from .spell_additional_cost_templates import FixedSacrificeAdditionalCostTemplate


def _closed_unpriced_body(program, *, prefix, recognizers):
    template = str(program.provenance.get('template_id') or '')
    if not template.startswith(prefix):
        return False
    result = replace(program, cost_schema=None,
        provenance={**program.provenance, 'template_id': template[len(prefix):]},
        coverage=[value for value in program.coverage if value != 'cr-601-casting-spells'])
    return any(recognizer(result) for recognizer in recognizers)


def closed_fixed_sacrifice_spell_result(program, *, recognizers):
    schema=program.cost_schema
    if not isinstance(schema,Mapping) or set(schema)!={'additional_costs'}:return False
    rows=schema['additional_costs']
    if not isinstance(rows,(list,tuple)) or len(rows)!=1:return False
    try:
        cost=fixed_sacrifice_additional_cost(rows[0])
        if cost is None:return False
        price=FixedSacrificeAdditionalCostTemplate(cost.predicate.types_any)
    except (TypeError,ValueError):return False
    if schema!=price.cost_schema or 'casting.additional_cost.fixed_sacrifice' not in program.capability_dependencies:return False
    return _closed_unpriced_body(program, prefix=price.template_id+'+', recognizers=recognizers)


def closed_alternative_damage_death_exile_result(program, *, recognizers):
    from ..damage_death_exile_model import DAMAGE_DEATH_EXILE_MECHANIC
    from ..rules.casting_additional_cost_groups import fixed_alternative_additional_cost_node_capabilities

    if (DAMAGE_DEATH_EXILE_MECHANIC not in program.coverage or program.event != 'resolve'
            or not program.ability_id.startswith('spell:')):
        return False
    required = fixed_alternative_additional_cost_node_capabilities(cost_schema=program.cost_schema)
    if not required or not set(required).issubset(program.capability_dependencies):
        return False
    price, separator, _body = str(program.provenance.get('template_id') or '').partition('+')
    if not separator or not price.startswith('spell-additional-cost-fixed-alternative-'):
        return False
    return _closed_unpriced_body(program, prefix=price+separator, recognizers=recognizers)
