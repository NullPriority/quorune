from __future__ import annotations

"""Payment-bound result validation and original-spell scalar lookup."""

from collections.abc import Mapping

from ..spell_mana_conditions import ManaSpentCondition,mana_type_from_reference,PAID_MANA_CONDITION_CAPABILITY
from ..spell_mana_counts import SpellManaSpentCounts,MANA_SPENT_COUNTS_CAPABILITY


def resolve_cast_mana_units(host,reference,item):
    mana_type=mana_type_from_reference(reference)
    if item.kind=='spell_copy':return 0
    card=host.state.cards.get(item.card_object_id or '')
    fact=getattr(item,'mana_spent_counts',None)
    if item.kind!='spell' or card is None or card.zone!='stack' or not isinstance(fact,SpellManaSpentCounts):
        raise ValueError('Paid-mana conditions require the original spell payment facts')
    return fact.count_for(card,mana_type)


def paid_mana_resolution_dependencies(program):
    effects=[e for e in program.effects if e.get('op')=='apply_if_public_condition' and e.get('schema_version') in {6,7}]
    if not effects:return ()
    from .resolution_condition_shapes import resolution_condition_node_capabilities
    required=resolution_condition_node_capabilities(effects=program.effects,target_schema=program.target_schema,
        mechanic_ids=program.coverage,cost_schema=program.cost_schema)
    if not required:raise ValueError('Paid-mana results lack complete prefix, branch or target closure')
    return required
