from __future__ import annotations

"""Ordered dispatch for closed casting-cost keyword productions."""

from .bestow_nodes import fixed_mana_bestow_keyword_node
from .flashback_nodes import ordinary_fixed_mana_flashback_keyword_node
from .cast_lifecycle_nodes import fixed_cast_lifecycle_keyword_node
from .combat_entry_activation_nodes import fixed_ninjutsu_keyword_node, fixed_encore_keyword_node
from .myriad_nodes import fixed_myriad_keyword_node
from .public_alternative_cost_nodes import fixed_public_alternative_cost_keyword_node
from .counter_keyword_activation_nodes import fixed_counter_keyword_activation_node
from .conspire_nodes import conspire_static_node


def casting_keyword_node(*, record, face_id, printed_power, **values):
    if values['mechanics'] == ('cleave',):
        from .cleave_spell_faces import residual_cleave_keyword_node
        return residual_cleave_keyword_node(**values)
    if values['mechanics'] == ('overload',):
        from .overload_spell_faces import residual_overload_keyword_node
        return residual_overload_keyword_node(**values)
    if values['mechanics'] == ('conspire',):
        return conspire_static_node(record=record, **{key: value for key, value in values.items() if key != 'mechanics'})
    for lower in (fixed_mana_bestow_keyword_node, ordinary_fixed_mana_flashback_keyword_node):
        node = lower(**values)
        if node is not None:
            return node
    lifecycle = fixed_cast_lifecycle_keyword_node(record=record, face_id=face_id, **values)
    if lifecycle is not None:
        return lifecycle
    for lower in (fixed_ninjutsu_keyword_node, fixed_encore_keyword_node, fixed_myriad_keyword_node,
            fixed_public_alternative_cost_keyword_node):
        node = lower(**values)
        if node is not None:
            return node
    return fixed_counter_keyword_activation_node(**values, printed_power=printed_power)
