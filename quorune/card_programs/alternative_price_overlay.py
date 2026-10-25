from __future__ import annotations

from ..public_alternative_costs import FixedPublicAlternativeCostSpec, FIXED_PUBLIC_ALTERNATIVE_COST_HANDLER_ID
from ..rules.capabilities import targeted_exile_node_capabilities


def shadowed_reviewed_zero_price_keys(record, generated_programs, reviewed_programs):
    """Replace only source-equivalent reviewed price/exile compound views."""
    generated = tuple(generated_programs)
    shadowed = set()
    for current in generated:
        if (current.oracle_id != record.oracle_id or current.trust_level != 'trusted' or not current.capability_closure
                or current.capability_closure.get('trusted') is not True or current.event != 'resolve'
                or current.active_zone != 'stack' or current.cost_schema is not None or current.handlers):
            continue
        if current.target_schema != {'zones': ['battlefield'], 'categories': ['permanent'], 'count': 1, 'types_any': ['creature']}:
            continue
        dependencies = targeted_exile_node_capabilities(effects=current.effects,
            target_schema=current.target_schema, mechanic_ids=current.coverage)
        if not dependencies or not set(dependencies).issubset(current.capability_dependencies):
            continue
        prices = [p for p in generated if p.oracle_id == record.oracle_id and p.trust_level == 'trusted' and p.event == 'cast.cost'
            and p.active_zone == 'all' and p.capability_closure and p.capability_closure.get('trusted') is True
            and len(p.handlers) == 1 and p.handlers[0].get('handler_id') == FIXED_PUBLIC_ALTERNATIVE_COST_HANDLER_ID]
        if len(prices) != 1:
            continue
        spec = FixedPublicAlternativeCostSpec.from_dict(prices[0].handlers[0]['alternative_cost'])
        if spec.condition.kind.value != 'controlled_commander' or spec.cost_text != '{0}':
            continue
        for old in reviewed_programs:
            if (old.key != current.key or old.oracle_id != record.oracle_id or old.trust_level != 'trusted'
                    or str(old.provenance.get('authored_by', '')).startswith('oracle-ir-v')):
                continue
            if any(getattr(old, field) != getattr(current, field) for field in
                    ('active_zone', 'event', 'event_condition', 'handlers', 'destination', 'requires_arbiter')):
                continue
            if any(not current.provenance.get(field) or old.provenance.get(field) != current.provenance[field]
                    or prices[0].provenance.get(field) != current.provenance[field]
                    for field in ('source_oracle_hash', 'source_rulings_hash')):
                continue
            if old.target_schema != {'zones': ['battlefield'], 'categories': ['permanent'], 'creature': True, 'count': 1}:
                continue
            if list(old.effects) != [{'op': 'exile', 'card': '$target.0'}]:
                continue
            if old.cost_schema != {'alternate_costs': [{'id': 'commander_free', 'kind': 'commander_presence',
                    'label': 'Cast without paying mana while controlling a commander', 'requirements': {},
                    'condition': {'control_commander': True}}]}:
                continue
            shadowed.add(old.key)
    return shadowed
