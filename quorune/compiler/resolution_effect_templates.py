from __future__ import annotations
from .counter_resolution_effect_templates import counter_resolution_effect_template
from .public_quantity_mana_effects import public_quantity_mana_effect_template
from .whole_hand_discard_templates import whole_hand_discard_effect_template, whole_hand_discard_draw_sequence_template, whole_hand_discard_public_draw_sequence
from .hand_entry_templates import fixed_draw_then_hand_entry_template
from .defender_permission_templates import temporary_defender_permission_template
from .as_unblocked_templates import temporary_as_unblocked_template

from typing import Any, Mapping, Sequence

from ..attachment_references import AttachmentReferenceKind
from .affected_player_discard_templates import (
    fixed_affected_player_discard_effect_template,
)
from .affected_player_sacrifice_templates import (
    fixed_affected_player_sacrifice_effect_template,
)
from .fixed_attachment_templates import fixed_source_attachment_effect_template
from .fixed_control_templates import fixed_control_effect_template, fixed_control_set_effect_template
from .linked_exile_return_templates import linked_exile_return_effect_template
from .amass_templates import fixed_amass_effect_template
from .bolster_templates import fixed_bolster_effect_template
from .counter_placement_templates import (
    fixed_player_counter_placement_effect_template,
    support_counter_placement_effect_template,
)
from .counter_removal_templates import (
    all_counter_removal_effect_template,
    fixed_counter_removal_effect_template,
)
from .counter_templates import targeted_counter_effect_template, targeted_controller_payment_template
from .creature_power_damage_templates import (
    fixed_creature_power_damage_effect_template,
)
from .damage_templates import fixed_damage_effect_template
from .destruction_templates import destruction_effect_template
from .exile_templates import targeted_exile_effect_template
from .fixed_target_effect_sequences import (
    fixed_source_characteristics_effect_template,
    fixed_target_characteristics_effect_template,
    fixed_target_effect_sequence_template,
    fixed_target_zone_object_keyword_sequence_template,
)
from .fixed_homogeneous_target_sets import (
    fixed_homogeneous_target_set_effect_template,
)
from .fixed_owner_zone_move_templates import (
    fixed_owner_zone_move_effect_template,
)
from .fixed_source_effect_sequences import (
    fixed_source_effect_sequence_template,
)
from .fixed_counter_controller_effect_sequences import (
    fixed_counter_controller_effect_sequence_template,
)
from .proliferate_templates import single_proliferate_effect_template
from .reanimation_templates import fixed_target_reanimation_effect_template
from .public_zone_move_templates import public_zone_move_effect_template
from .return_to_hand_templates import (
    targeted_own_graveyard_return_to_hand_effect_template,
    targeted_return_to_hand_effect_template,
)
from .self_counter_keyword_actions import (
    fixed_self_counter_keyword_action_template,
)
from .temporary_declaration_templates import (
    temporary_declaration_restriction_effect_template,
)
from .temporary_target_interactions import temporary_target_interaction_effect_template


CompiledEffectTemplate = tuple[
    str | None,
    tuple[Mapping[str, Any], ...],
    Mapping[str, Any] | None,
    tuple[str, ...],
]


def _attachment_or_owner_zone_move(
    text: str,
    card_name: str,
    source_is_permanent: bool | None,
    source_attachment_relation: AttachmentReferenceKind | None,
):
    return fixed_source_attachment_effect_template(
        text,
        source_attachment_relation=source_attachment_relation,
    ) or fixed_owner_zone_move_effect_template(
        text,
        card_name=card_name,
        source_is_permanent=source_is_permanent,
        source_attachment_relation=source_attachment_relation,
    ) or fixed_creature_power_damage_effect_template(
        text,
        card_name=card_name,
        source_attachment_relation=source_attachment_relation,
    )


_DIRECT_RESOLUTION_EFFECT_COMPILERS = (
    fixed_target_reanimation_effect_template, public_zone_move_effect_template, temporary_target_interaction_effect_template,
    destruction_effect_template,
    targeted_exile_effect_template,
    targeted_return_to_hand_effect_template,
    targeted_own_graveyard_return_to_hand_effect_template,
    targeted_counter_effect_template,
    targeted_controller_payment_template,
)


def _source_context_resolution_template(
    text: str, *, card_name: str, source_is_permanent: bool | None,
    source_card_types: Sequence[str],
) -> CompiledEffectTemplate | None:
    as_unblocked=temporary_as_unblocked_template(text,source_name=card_name,source_is_permanent=source_is_permanent,source_card_types=source_card_types)
    if as_unblocked is not None:return as_unblocked
    permission=temporary_defender_permission_template(text,source_name=card_name,source_is_permanent=source_is_permanent,source_card_types=tuple(source_card_types))
    if permission is not None:return permission
    hand_entry=fixed_draw_then_hand_entry_template(text)
    if hand_entry is not None:return hand_entry
    discard = whole_hand_discard_effect_template(text) or whole_hand_discard_draw_sequence_template(text) or whole_hand_discard_public_draw_sequence(text,source_name=card_name)
    if discard is not None:
        return discard
    mana = public_quantity_mana_effect_template(text, source_name=card_name)
    if mana is not None:
        return mana
    control = fixed_control_effect_template(
        text, card_name=card_name, source_is_permanent=source_is_permanent,
        source_card_types=tuple(source_card_types),
    ) or fixed_control_set_effect_template(text)
    if control is not None:
        return control.compiled()
    return linked_exile_return_effect_template(text, card_name=card_name, source_is_permanent=source_is_permanent)


def typed_resolution_effect_template(
    text: str,
    *,
    card_name: str,
    source_is_permanent: bool | None = None,
    source_card_types: Sequence[str] = (),
    source_attachment_relation: AttachmentReferenceKind | None = None,
) -> CompiledEffectTemplate | None:
    """Lower closed typed resolution-effect families."""

    contextual = _source_context_resolution_template(text, card_name=card_name, source_is_permanent=source_is_permanent, source_card_types=source_card_types)
    if contextual is not None:
        return contextual

    fixed_counter_controller_sequence = (
        fixed_counter_controller_effect_sequence_template(
            text,
            card_name=card_name,
        )
    )
    if fixed_counter_controller_sequence is not None:
        return fixed_counter_controller_sequence.compiled()
    attachment = _attachment_or_owner_zone_move(
        text, card_name, source_is_permanent, source_attachment_relation
    )
    if attachment is not None:
        return attachment.compiled()
    fixed_damage = fixed_damage_effect_template(text, card_name=card_name)
    if fixed_damage is not None:
        return fixed_damage.compiled()
    self_counter_action = fixed_self_counter_keyword_action_template(text)
    if self_counter_action is not None:
        return self_counter_action.compiled()
    proliferate = single_proliferate_effect_template(text)
    if proliferate is not None:
        return proliferate.compiled()
    bolster = fixed_bolster_effect_template(text)
    if bolster is not None:
        return bolster.compiled()
    amass = fixed_amass_effect_template(text)
    if amass is not None:
        return amass.compiled()
    if source_is_permanent is not None:
        support = support_counter_placement_effect_template(
            text,
            source_is_permanent=source_is_permanent,
        )
        if support is not None:
            return support.compiled()
    fixed_player_counter_placement = (
        fixed_player_counter_placement_effect_template(text)
    )
    if fixed_player_counter_placement is not None:
        return fixed_player_counter_placement.compiled()
    affected_player_sacrifice = (
        fixed_affected_player_sacrifice_effect_template(text)
    )
    if affected_player_sacrifice is not None:
        return affected_player_sacrifice.compiled()
    affected_player_discard = fixed_affected_player_discard_effect_template(
        text
    )
    if affected_player_discard is not None:
        return affected_player_discard.compiled()
    fixed_homogeneous_target_set = (
        fixed_homogeneous_target_set_effect_template(text)
    )
    if fixed_homogeneous_target_set is not None:
        return fixed_homogeneous_target_set.compiled()
    counter_placement = counter_resolution_effect_template(text, card_name=card_name,
        source_is_permanent=source_is_permanent, source_attachment_relation=source_attachment_relation)
    if counter_placement is not None:
        return counter_placement
    all_counter_removal = all_counter_removal_effect_template(text)
    if all_counter_removal is not None:
        return all_counter_removal.compiled()
    fixed_counter_removal = fixed_counter_removal_effect_template(text)
    if fixed_counter_removal is not None:
        return fixed_counter_removal.compiled()
    fixed_source_characteristics = (
        fixed_source_characteristics_effect_template(
            text,
            source_is_permanent=source_is_permanent,
            source_card_types=tuple(source_card_types), source_name=card_name,
        )
    )
    if fixed_source_characteristics is not None:
        return fixed_source_characteristics.compiled()
    fixed_target_characteristics = (
        fixed_target_characteristics_effect_template(text)
    )
    if fixed_target_characteristics is not None:
        return fixed_target_characteristics.compiled()
    temporary_declaration_restriction = (
        temporary_declaration_restriction_effect_template(
            text,
            card_name=card_name,
            allow_source=source_is_permanent is True,
        )
    )
    if temporary_declaration_restriction is not None:
        return temporary_declaration_restriction.compiled()
    fixed_source_sequence = fixed_source_effect_sequence_template(
        text,
        card_name=card_name,
        source_is_permanent=source_is_permanent,
    )
    if fixed_source_sequence is not None:
        return fixed_source_sequence.compiled()
    fixed_zone_object_keyword_sequence = (
        fixed_target_zone_object_keyword_sequence_template(
            text,
            card_name=card_name,
        )
    )
    if fixed_zone_object_keyword_sequence is not None:
        return fixed_zone_object_keyword_sequence.compiled()
    fixed_target_sequence = fixed_target_effect_sequence_template(
        text,
        card_name=card_name,
    )
    if fixed_target_sequence is not None:
        return fixed_target_sequence.compiled()
    for compiler in _DIRECT_RESOLUTION_EFFECT_COMPILERS:
        compiled = compiler(text)
        if compiled is not None:
            return compiled.compiled()
    return None


__all__ = ["CompiledEffectTemplate", "typed_resolution_effect_template"]
