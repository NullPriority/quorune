from __future__ import annotations

"""Payment-bound result validation and original-spell scalar lookup."""

from collections.abc import Mapping

from ..spell_mana_conditions import (
    ACTUAL_CAST_MANA_TOTAL_KIND, mana_type_from_reference, validate_actual_cast_mana_total_reference,
)
from ..spell_mana_counts import SpellManaSpentCounts,MANA_SPENT_COUNTS_CAPABILITY


def resolve_cast_mana_units(host,reference,item):
    if isinstance(reference, Mapping) and reference.get('kind') == ACTUAL_CAST_MANA_TOTAL_KIND:
        validate_actual_cast_mana_total_reference(reference)
        mana_type = None
    else:
        mana_type = mana_type_from_reference(reference)
    if item.kind=='spell_copy':return 0
    card=host.state.cards.get(item.card_object_id or '')
    fact=getattr(item,'mana_spent_counts',None)
    if item.kind!='spell' or card is None or card.zone!='stack' or not isinstance(fact,SpellManaSpentCounts):
        raise ValueError('Paid-mana conditions require the original spell payment facts')
    return fact.total_for(card) if mana_type is None else fact.count_for(card, mana_type)


def paid_mana_resolution_dependencies(program):
    if MANA_SPENT_COUNTS_CAPABILITY in program.capability_dependencies:
        from ..variable_library_selection_model import uses_actual_mana_spent_library_selection
        from .effect_shape_walkers import nested_effects
        from .variable_library_selection_shapes import actual_mana_spent_library_runtime_dependencies

        if any(uses_actual_mana_spent_library_selection(effect)
               for effect in nested_effects((program.effects, program.handlers))):
            return actual_mana_spent_library_runtime_dependencies(program)
    effects=[e for e in program.effects if e.get('op')=='apply_if_public_condition' and e.get('schema_version') in {6,7}]
    if not effects:return ()
    if len(effects) > 1 and (program.event, program.active_zone) != ("resolve", "stack"):
        raise ValueError("Independent actual-mana conditions require one resolving spell")
    from .resolution_condition_shapes import resolution_condition_node_capabilities
    required=resolution_condition_node_capabilities(effects=program.effects,target_schema=program.target_schema,
        mechanic_ids=program.coverage,cost_schema=program.cost_schema)
    if not required:raise ValueError('Paid-mana results lack complete prefix, branch or target closure')
    return required
