from __future__ import annotations

"""One resolution grammar dispatcher for ordinary and named counter placements."""

from .counter_doubling_templates import named_counter_doubling_effect_template
from .counter_placement_group_templates import fixed_counter_placement_group_effect_template
from .counter_placement_templates import (fixed_counter_placement_batch_effect_template,
    fixed_counter_placement_effect_template, fixed_counter_placement_set_effect_template,
    fixed_counter_placement_target_set_effect_template)


def counter_resolution_effect_template(text, *, card_name, source_is_permanent, source_attachment_relation):
    fixed_counter_placement_target_set = (
        fixed_counter_placement_target_set_effect_template(text)
    )
    if fixed_counter_placement_target_set is not None:
        return fixed_counter_placement_target_set.compiled()
    fixed_counter_placement_set = fixed_counter_placement_set_effect_template(
        text
    )
    if fixed_counter_placement_set is not None:
        return fixed_counter_placement_set.compiled()
    doubling = named_counter_doubling_effect_template(text, card_name=card_name,
        source_attachment_relation=source_attachment_relation)
    if doubling is not None:
        return doubling
    fixed_counter_placement_group = (
        fixed_counter_placement_group_effect_template(
            text,
            card_name=card_name,
            source_is_permanent=source_is_permanent,
        )
    )
    if fixed_counter_placement_group is not None:
        return fixed_counter_placement_group.compiled()
    fixed_counter_placement_batch = fixed_counter_placement_batch_effect_template(
        text,
        card_name=card_name,
        source_attachment_relation=source_attachment_relation,
    )
    if fixed_counter_placement_batch is not None:
        return fixed_counter_placement_batch.compiled()
    fixed_counter_placement = fixed_counter_placement_effect_template(
        text,
        card_name=card_name,
        source_attachment_relation=source_attachment_relation,
    )
    if fixed_counter_placement is not None:
        return fixed_counter_placement.compiled()
    return None
