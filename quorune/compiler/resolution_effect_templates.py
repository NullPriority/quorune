from __future__ import annotations
from ..random_discard import random_discard_effect_template
from .counter_resolution_effect_templates import counter_resolution_effect_template
from .public_quantity_mana_effects import public_quantity_mana_effect_template
from .fixed_resolution_mana import fixed_resolution_mana_effect_template
from .extra_turn_templates import fixed_extra_turn_effect_template
from .whole_hand_discard_templates import whole_hand_discard_effect_template, whole_hand_discard_draw_sequence_template, whole_hand_discard_public_draw_sequence
from .discard_result_templates import discard_result_draw_template
from .hand_entry_templates import fixed_draw_then_hand_entry_template
from .defender_permission_templates import temporary_defender_permission_template
from .as_unblocked_templates import temporary_as_unblocked_template
from .toughness_assignment_templates import temporary_toughness_assignment_template
from .connive_templates import fixed_connive_effect_template
from .goad_templates import fixed_goad_effect_template
from .block_capacity_templates import temporary_block_capacity_template

from typing import Any, Mapping, Sequence

from ..attachment_references import AttachmentReferenceKind
from .affected_player_discard_templates import (
    fixed_affected_player_discard_effect_template,
)
from . import affected_player_discard_templates as _discard
from .affected_player_sacrifice_templates import (
    fixed_affected_player_sacrifice_effect_template,
)
from .fixed_attachment_templates import fixed_source_attachment_effect_template
from .fixed_control_templates import fixed_control_effect_template, fixed_control_set_effect_template
from .linked_exile_return_templates import linked_exile_return_effect_template
from .conditional_exile_templates import conditional_source_exile_template
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
from .source_keyword_choices import source_keyword_choice_template
from .source_protection_choices import source_protection_choice_template
from .source_fixed_protection import source_fixed_protection_template


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
    from .attached_destruction import attached_destruction_template
    from .exile_templates import attached_exile_effect_template
    return attached_exile_effect_template(text,source_attachment_relation=source_attachment_relation) or attached_destruction_template(text,source_attachment_relation=source_attachment_relation) or fixed_source_attachment_effect_template(
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
    goad = fixed_goad_effect_template(text)
    if goad is not None:return goad
    connive = fixed_connive_effect_template(text, source_name=card_name, source_is_permanent=source_is_permanent)
    if connive is not None:return connive
    extra_turn = fixed_extra_turn_effect_template(text)
    if extra_turn is not None:return extra_turn
    capacity = temporary_block_capacity_template(text, source_name=card_name,
        source_is_permanent=source_is_permanent, source_card_types=source_card_types)
    if capacity is not None:return capacity
    toughness=temporary_toughness_assignment_template(text)
    if toughness is not None:return toughness
    as_unblocked=temporary_as_unblocked_template(text,source_name=card_name,source_is_permanent=source_is_permanent,source_card_types=source_card_types)
    if as_unblocked is not None:return as_unblocked
    permission=temporary_defender_permission_template(text,source_name=card_name,source_is_permanent=source_is_permanent,source_card_types=tuple(source_card_types))
    if permission is not None:return permission
    hand_entry=fixed_draw_then_hand_entry_template(text)
    if hand_entry is not None:return hand_entry
    discard = discard_result_draw_template(text) or random_discard_effect_template(text) or whole_hand_discard_effect_template(text) or whole_hand_discard_draw_sequence_template(text) or whole_hand_discard_public_draw_sequence(text,source_name=card_name)
    if discard is not None:
        return discard
    mana = fixed_resolution_mana_effect_template(text) or public_quantity_mana_effect_template(text, source_name=card_name)
    if mana is not None:
        return mana
    control = fixed_control_effect_template(
        text, card_name=card_name, source_is_permanent=source_is_permanent,
        source_card_types=tuple(source_card_types),
    ) or fixed_control_set_effect_template(text)
    if control is not None:
        return control.compiled()
    conditional=conditional_source_exile_template(text,card_name=card_name,source_is_permanent=source_is_permanent)
    if conditional is not None:return conditional.compiled()
    return linked_exile_return_effect_template(text, card_name=card_name, source_is_permanent=source_is_permanent)


def _source_independent_resolution_template(text: str) -> CompiledEffectTemplate | None:
    """Retain the precedence of leaves that do not inspect source context."""
    from .untargeted_untap_templates import fixed_land_untap_choice_template
    untap = fixed_land_untap_choice_template(text)
    if untap is not None:return untap
    from .fixed_top_order_templates import fixed_top_order_template
    top_order=fixed_top_order_template(text)
    if top_order is not None:return top_order
    from .temporary_player_rule_templates import temporary_player_rule_template
    cast_restriction=temporary_player_rule_template(text)
    if cast_restriction is not None:return cast_restriction
    from .random_graveyard_returns import random_graveyard_return_template
    random_return=random_graveyard_return_template(text)
    if random_return is not None:return random_return
    from .manifest_templates import manifest_effect_template
    manifest = manifest_effect_template(text)
    if manifest is not None:
        return manifest
    return None


def typed_resolution_effect_template(
    text: str,
    *,
    card_name: str,
    source_is_permanent: bool | None = None,
    source_card_types: Sequence[str] = (),
    source_attachment_relation: AttachmentReferenceKind | None = None,
) -> CompiledEffectTemplate | None:
    """Lower closed typed resolution-effect families."""
    independent = _source_independent_resolution_template(text)
    if independent is not None:
        return independent

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
    if (controller_discard := _discard.standalone_controller_discard_effect_template(text)) is not None:
        return controller_discard
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
    protection_choice = source_protection_choice_template(text, source_is_permanent=source_is_permanent,
        source_card_types=tuple(source_card_types), source_name=card_name)
    if protection_choice is not None:
        return protection_choice
    fixed_protection = source_fixed_protection_template(text, source_is_permanent=source_is_permanent,
        source_card_types=tuple(source_card_types), source_name=card_name)
    if fixed_protection is not None:
        return fixed_protection
    source_choice = source_keyword_choice_template(text, source_is_permanent=source_is_permanent,
        source_card_types=tuple(source_card_types), source_name=card_name)
    if source_choice is not None:
        return source_choice.compiled()
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
