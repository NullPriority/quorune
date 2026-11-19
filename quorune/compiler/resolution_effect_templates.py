from __future__ import annotations
from .fixed_hand_putback_templates import fixed_draw_hand_putback_template
from .hand_library_redraw_templates import hand_library_redraw_template
from ..random_discard import random_discard_effect_template
from .counter_resolution_effect_templates import counter_resolution_effect_template
from .public_quantity_mana_effects import public_quantity_mana_effect_template
from .fixed_resolution_mana import fixed_resolution_mana_effect_template
from .extra_turn_templates import fixed_extra_turn_effect_template
from .scheduled_turn_skip_templates import scheduled_turn_skip_effect_template
from .tap_state_templates import next_player_untap_effect_template
from .whole_hand_discard_templates import whole_hand_discard_effect_template, whole_hand_discard_draw_sequence_template, whole_hand_discard_public_draw_sequence
from .discard_result_templates import discard_result_draw_template
from .day_night_nodes import fixed_day_night_designation_template
from .next_spell_cost_templates import next_spell_cost_reduction_template
from .hand_entry_templates import fixed_draw_then_hand_entry_template, fixed_private_hand_entry_followup_template
from .attacking_card_entry_templates import fixed_attacking_card_entry_template
from .defender_permission_templates import temporary_defender_permission_template
from .as_unblocked_templates import temporary_as_unblocked_template
from .toughness_assignment_templates import temporary_toughness_assignment_template
from .connive_templates import fixed_connive_effect_template
from .endure_templates import fixed_endure_effect_template
from .earthbend_templates import earthbend_effect_template
from .blight_templates import fixed_blight_effect_template
from .goad_templates import fixed_goad_effect_template
from .detain_templates import fixed_detain_effect_template, fixed_direct_action_prohibition_template
from .suspect_templates import suspect_effect_template
from .block_capacity_templates import temporary_block_capacity_template

from typing import Any, Mapping, Sequence

from ..attachment_references import AttachmentReferenceKind
from .affected_player_discard_templates import (
    fixed_affected_player_discard_effect_template,
    fixed_affected_player_hand_exile_effect_template,
)
from . import affected_player_discard_templates as _discard
from .affected_player_sacrifice_templates import (
    fixed_affected_player_sacrifice_effect_template,
)
from .fixed_attachment_templates import (
    created_token_attachment_effect_template,
    fixed_source_attachment_effect_template,
    targeted_equipment_attachment_template,
)
from .fixed_control_templates import fixed_control_effect_template, fixed_control_set_effect_template
from .recipient_control_templates import recipient_control_effect_template
from .owner_control_templates import owner_control_effect_template
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
from .counter_templates import (
    spell_targeting_counter_effect_template, targeted_counter_effect_template, targeted_controller_payment_template,
)
from .creature_power_damage_templates import (
    fixed_creature_power_damage_effect_template,
)
from .damage_templates import fixed_damage_effect_template
from .destruction_templates import destruction_effect_template
from .exile_templates import stack_spell_exile_template, targeted_exile_effect_template
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
from .reanimation_entry_counter_templates import fixed_reanimation_entry_counter_template
from .reanimation_followup_templates import fixed_reanimation_followup_template
from .public_zone_move_templates import public_zone_move_effect_template
from .return_to_hand_templates import (
    targeted_own_graveyard_return_to_hand_effect_template,
    targeted_public_graveyard_return_to_hand_effect_template,
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
from .fixed_target_color import fixed_target_color_template


CompiledEffectTemplate = tuple[
    str | None,
    tuple[Mapping[str, Any], ...],
    Mapping[str, Any] | None,
    tuple[str, ...],
]


def _attachment_or_zone_result(
    text: str,
    card_name: str,
    source_is_permanent: bool | None,
    source_attachment_relation: AttachmentReferenceKind | None,
) -> CompiledEffectTemplate | None:
    from .attached_destruction import attached_destruction_template
    from .exile_templates import attached_exile_effect_template
    from .fixed_resolution_characteristics import (
        fixed_attached_keyword_effect_template, fixed_attached_stat_effect_template,
    )
    attached_characteristic = (
        fixed_attached_stat_effect_template(text, source_attachment_relation=source_attachment_relation)
        or fixed_attached_keyword_effect_template(text, source_attachment_relation=source_attachment_relation)
    )
    if attached_characteristic is not None:
        return attached_characteristic
    created_attachment = created_token_attachment_effect_template(
        text, source_attachment_relation=source_attachment_relation,
    )
    if created_attachment is not None:
        return created_attachment
    targeted_equipment = targeted_equipment_attachment_template(
        text, card_name=card_name, source_is_permanent=source_is_permanent,
    )
    if targeted_equipment is not None:
        return targeted_equipment
    template = (
        attached_exile_effect_template(text, source_attachment_relation=source_attachment_relation)
        or attached_destruction_template(text, source_attachment_relation=source_attachment_relation)
        or fixed_source_attachment_effect_template(text, source_attachment_relation=source_attachment_relation)
        or fixed_owner_zone_move_effect_template(
            text,
            card_name=card_name,
            source_is_permanent=source_is_permanent,
            source_attachment_relation=source_attachment_relation,
        )
        or fixed_creature_power_damage_effect_template(
            text,
            card_name=card_name,
            source_attachment_relation=source_attachment_relation,
        )
    )
    return template.compiled() if template is not None else None


_DIRECT_RESOLUTION_EFFECT_COMPILERS = (
    fixed_draw_hand_putback_template,
    hand_library_redraw_template,
    fixed_reanimation_followup_template,
    fixed_reanimation_entry_counter_template,
    fixed_target_reanimation_effect_template, public_zone_move_effect_template, temporary_target_interaction_effect_template,
    destruction_effect_template,
    targeted_exile_effect_template,
    targeted_return_to_hand_effect_template,
    targeted_own_graveyard_return_to_hand_effect_template,
    targeted_public_graveyard_return_to_hand_effect_template,
    targeted_counter_effect_template,
    spell_targeting_counter_effect_template,
    targeted_controller_payment_template,
)


def _single_stack_target_change_template(text, *, source_is_permanent):
    from . import regex_working_set as re
    from ..stack_target_change_model import SingleStackTargetChangeSpec

    if source_is_permanent is not False:
        return None
    match = re.fullmatch(
        r"Change the target of target spell(?P<ability> or ability)? with a single target\.?",
        text.strip(), re.I,
    )
    if match is None:
        return None
    spec = SingleStackTargetChangeSpec(bool(match["ability"]))
    return "mandatory-single-stack-target-change-v1", spec.effects, spec.target_schema, ("cr-115-targets",)


def _optional_stack_target_change_template(text, *, source_is_permanent):
    from . import regex_working_set as re
    from ..stack_target_change_model import OptionalStackTargetChangeSpec

    if source_is_permanent is not False:
        return None
    match = re.fullmatch(
        r"You may choose new targets for target spell(?P<ability> or ability)?\.?",
        text.strip(), re.I,
    )
    if match is None:
        return None
    spec = OptionalStackTargetChangeSpec(bool(match["ability"]))
    return "optional-stack-target-change-v1", spec.effects, spec.target_schema, ("cr-115-targets",)


def _stack_spell_control_template(text, *, source_is_permanent):
    from . import regex_working_set as re
    from ..stack_target_change_model import StackSpellControlSpec

    if source_is_permanent is not False:
        return None
    normalized = " ".join(text.strip().split())
    match = re.fullmatch(
        r"Gain control of target (?P<noncreature>noncreature )?spell\. You may choose new targets for it\.?"
        r"(?: \((?P<reminder>[^()]+)\))?",
        normalized, re.I,
    )
    if match is None:
        return None
    noncreature = bool(match["noncreature"])
    expected = ("If that spell is an artifact, enchantment, or planeswalker, the permanent enters under your control."
                if noncreature else "If that spell becomes a permanent, it enters under your control.")
    if match["reminder"] is not None and match["reminder"].casefold() != expected.casefold():
        return None
    spec = StackSpellControlSpec(noncreature)
    return "stack-spell-control-and-optional-targets-v1", spec.effects, spec.target_schema, ("cr-115-targets",)


def _stack_target_change_payment_template(text, *, source_is_permanent):
    from . import regex_working_set as re
    from ..stack_target_change_model import SingleStackTargetChangeSpec, stack_target_change_payment_effect

    if source_is_permanent is not False or re.fullmatch(
        r"Change the target of target spell with a single target unless that spell['’]s controller pays \{2\}\.?",
        text.strip(), re.I,
    ) is None:
        return None
    return ("stack-target-change-controller-payment-v1", (stack_target_change_payment_effect(),),
            SingleStackTargetChangeSpec(False).target_schema, ("cr-115-targets",))


def _source_context_resolution_template(
    text: str, *, card_name: str, source_is_permanent: bool | None,
    source_card_types: Sequence[str],
) -> CompiledEffectTemplate | None:
    stack_change = (
        _single_stack_target_change_template(text, source_is_permanent=source_is_permanent)
        or _optional_stack_target_change_template(text, source_is_permanent=source_is_permanent)
        or _stack_spell_control_template(text, source_is_permanent=source_is_permanent)
        or _stack_target_change_payment_template(text, source_is_permanent=source_is_permanent)
        or stack_spell_exile_template(text, source_is_permanent=source_is_permanent)
    )
    if stack_change is not None:
        return stack_change
    from .damage_permission_templates import turn_damage_prevention_template
    turn_damage = turn_damage_prevention_template(text, card_name=card_name, source_is_permanent=source_is_permanent)
    if turn_damage is not None:return turn_damage
    from .source_sacrifice_templates import fixed_source_sacrifice_template
    sacrifice = fixed_source_sacrifice_template(text, source_name=card_name,
        source_is_permanent=source_is_permanent)
    if sacrifice is not None:return sacrifice
    color = fixed_target_color_template(text)
    if color is not None:
        return color
    detain = fixed_detain_effect_template(text)
    if detain is not None:
        return detain
    action_prohibition = fixed_direct_action_prohibition_template(text)
    if action_prohibition is not None:
        return action_prohibition
    goad = fixed_goad_effect_template(text)
    if goad is not None:return goad
    connive = fixed_connive_effect_template(text, source_name=card_name, source_is_permanent=source_is_permanent)
    if connive is not None:return connive
    endure = fixed_endure_effect_template(text, source_name=card_name, source_is_permanent=source_is_permanent)
    if endure is not None:
        return endure
    if "earthbend" in text.casefold():
        earthbend = earthbend_effect_template(text, source_name=card_name)
        if earthbend is not None:
            return earthbend
    blight = fixed_blight_effect_template(text)
    if blight is not None:
        return blight
    extra_turn = fixed_extra_turn_effect_template(text)
    if extra_turn is not None:return extra_turn
    scheduled_skip = scheduled_turn_skip_effect_template(text)
    if scheduled_skip is not None:
        return scheduled_skip
    untap_restriction = next_player_untap_effect_template(text)
    if untap_restriction is not None:
        return untap_restriction
    capacity = temporary_block_capacity_template(text, source_name=card_name,
        source_is_permanent=source_is_permanent, source_card_types=source_card_types)
    if capacity is not None:return capacity
    toughness=temporary_toughness_assignment_template(text)
    if toughness is not None:return toughness
    as_unblocked=temporary_as_unblocked_template(text,source_name=card_name,source_is_permanent=source_is_permanent,source_card_types=source_card_types)
    if as_unblocked is not None:return as_unblocked
    permission=temporary_defender_permission_template(text,source_name=card_name,source_is_permanent=source_is_permanent,source_card_types=tuple(source_card_types))
    if permission is not None:return permission
    from .hand_entry_collection_templates import private_hand_collection_template
    collection = private_hand_collection_template(text)
    if collection is not None:
        return collection
    hand_entry=(fixed_attacking_card_entry_template(text) or fixed_private_hand_entry_followup_template(text)
        or fixed_draw_then_hand_entry_template(text))
    if hand_entry is not None:return hand_entry
    next_cost=next_spell_cost_reduction_template(text,source_name=card_name,source_is_permanent=source_is_permanent)
    if next_cost is not None:return next_cost
    designation = fixed_day_night_designation_template(text)
    if designation is not None:return designation
    discard = discard_result_draw_template(text) or random_discard_effect_template(text) or whole_hand_discard_effect_template(text) or whole_hand_discard_draw_sequence_template(text) or whole_hand_discard_public_draw_sequence(text,source_name=card_name)
    if discard is not None:
        return discard
    mana = fixed_resolution_mana_effect_template(text) or public_quantity_mana_effect_template(text, source_name=card_name)
    if mana is not None:
        return mana
    recipient=owner_control_effect_template(text) or recipient_control_effect_template(text,card_name=card_name,source_is_permanent=source_is_permanent)
    if recipient is not None:return recipient
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
    from .bounded_hand_cast_templates import bounded_hand_cast_template
    hand_cast = bounded_hand_cast_template(text)
    if hand_cast is not None:return hand_cast.compiled()
    from .subtype_transform_templates import fixed_subtype_transform_effect_template
    transform = fixed_subtype_transform_effect_template(text)
    if transform is not None:return transform
    from .target_land_type_templates import fixed_target_land_type_template, target_land_type_choice_template
    fixed = fixed_target_land_type_template(text)
    if fixed is not None:
        return fixed
    choice = target_land_type_choice_template(text)
    if choice is not None:return choice
    from .graveyard_recycling_templates import graveyard_recycling_template
    recycling = graveyard_recycling_template(text)
    if recycling is not None:return recycling.compiled()
    from .search_allocation_templates import search_allocation_template
    allocation = search_allocation_template(text)
    if allocation is not None:return allocation
    from .conditional_search_templates import conditional_search_effect_template
    conditional_search=conditional_search_effect_template(text,source_name='This source')
    if conditional_search is not None:return conditional_search
    from .graveyard_copy_cast_templates import graveyard_copy_cast_template
    from .targeted_graveyard_cast_templates import targeted_graveyard_cast_template
    from .reanimation_entry_counter_templates import conditional_reanimation_entry_counter_template

    if (conditional_return := conditional_reanimation_entry_counter_template(text)) is not None:
        return conditional_return
    if (copy_cast := graveyard_copy_cast_template(text)) is not None:
        return copy_cast
    if (graveyard_cast := targeted_graveyard_cast_template(text)) is not None:
        return graveyard_cast
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


def _distinct_sacrifice_template(text: str) -> CompiledEffectTemplate | None:
    from ..rules.distinct_sacrifices import DISTINCT_SACRIFICE_FIELD, DISTINCT_SACRIFICE_ROLES

    material = " ".join(text.split()).casefold().removesuffix(".")
    sacrifice = "sacrifice a creature, an artifact, and a land"
    if material not in {sacrifice, "discard a card and " + sacrifice}:
        return None
    before = None
    mechanics = ("sacrifice",)
    if material != sacrifice:
        _, effects, _, discard_mechanics = _discard.standalone_controller_discard_effect_template("Discard a card.")
        before, = effects
        mechanics = (*discard_mechanics, *mechanics)
    effect = {"op": "choose_objects", "player": "$controller",
              DISTINCT_SACRIFICE_FIELD: {"schema_version": 1, "roles": list(DISTINCT_SACRIFICE_ROLES), "before": before}}
    return "distinct-permanent-sacrifices-v1", (effect,), None, mechanics


def _affected_player_object_effect_template(text: str) -> CompiledEffectTemplate | None:
    if distinct := _distinct_sacrifice_template(text):
        return distinct
    if 'life unless' in text.casefold():
        from .apnap_object_payment_templates import apnap_object_payment_template

        payment = apnap_object_payment_template(text)
        if payment is not None:
            return payment
    sacrifice = fixed_affected_player_sacrifice_effect_template(text)
    if sacrifice is not None:
        return sacrifice.compiled()
    controller_discard = _discard.standalone_controller_discard_effect_template(text)
    if controller_discard is not None:
        return controller_discard
    discard = fixed_affected_player_discard_effect_template(text)
    if discard is not None:
        return discard.compiled()
    return fixed_affected_player_hand_exile_effect_template(text)


def _temporary_characteristic_resolution_template(
    text: str, *, card_name: str, source_is_permanent: bool | None, source_card_types: Sequence[str],
) -> CompiledEffectTemplate | None:
    """Retain ordered source protection, keyword and source/target characteristic leaves."""
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
    return None


def _fixed_phase_out_template(text, *, card_name, source_is_permanent):
    from . import regex_working_set as re
    from ..phasing_model import PHASE_OUT_MECHANIC, phase_out_effect
    from ..rules.source_references import SourceReferenceSpec, source_self_permanent_type
    from .direct_target import direct_permanent_target_spec

    match = re.fullmatch(r"(?P<subject>.+?) phases out\.?", " ".join(text.split()), re.I)
    if match is None:
        return None
    subject = match["subject"]
    if subject.casefold() in ("target creature", "target creature you control", "target artifact, creature, or land"):
        target = direct_permanent_target_spec(subject)
        return ("fixed-target-phase-out-v1", (phase_out_effect(targeted=True),),
                target.to_target_schema(), (PHASE_OUT_MECHANIC, "cr-115-targets"))
    if source_is_permanent is True and (
        source_self_permanent_type(subject) is not None or SourceReferenceSpec(card_name).matches(subject)
    ):
        return ("fixed-source-phase-out-v1", (phase_out_effect(),), None, (PHASE_OUT_MECHANIC,))
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
    from .referenced_damage_templates import attachment_fixed_damage_template
    referenced=attachment_fixed_damage_template(text,card_name=card_name,
        source_is_permanent=source_is_permanent,relation=source_attachment_relation)
    if referenced is not None:return referenced
    from .scoped_target_colors import scoped_target_color_template
    color=scoped_target_color_template(text,compile_leaf=lambda clause:typed_resolution_effect_template(clause,
        card_name=card_name,source_is_permanent=source_is_permanent,source_card_types=source_card_types,
        source_attachment_relation=source_attachment_relation))
    if color is not None:return color
    suspect = suspect_effect_template(text, source_is_permanent=source_is_permanent, source_attachment_relation=source_attachment_relation)
    if suspect is not None:
        return suspect
    from .targeted_graveyard_exile_templates import targeted_graveyard_exile_template
    exile_choice = targeted_graveyard_exile_template(text)
    if exile_choice is not None:return exile_choice
    from .graveyard_return_choice_templates import graveyard_return_choice_template
    returned = graveyard_return_choice_template(text)
    if returned is not None:return returned
    from .combat_removal_templates import combat_removal_effect_template
    combat_removal = combat_removal_effect_template(text)
    if combat_removal is not None:
        return combat_removal
    phase_out = _fixed_phase_out_template(text, card_name=card_name, source_is_permanent=source_is_permanent)
    if phase_out is not None:
        return phase_out
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
    attachment = _attachment_or_zone_result(
        text, card_name, source_is_permanent, source_attachment_relation
    )
    if attachment is not None:
        return attachment
    from .controlled_return_templates import controlled_return_template
    controlled_return = controlled_return_template(text, source_is_permanent=source_is_permanent)
    if controlled_return is not None:
        return controlled_return
    from .fixed_dual_damage_templates import fixed_dual_damage_template
    from .fixed_damage_roles import fixed_damage_role_template
    roles=fixed_damage_role_template(text,card_name=card_name,source_is_permanent=source_is_permanent)
    if roles is not None:return roles
    dual_damage = fixed_dual_damage_template(text,card_name=card_name,source_is_permanent=source_is_permanent)
    if dual_damage is not None:return dual_damage.compiled()
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
    if (affected_object := _affected_player_object_effect_template(text)) is not None:
        return affected_object
    from .per_player_target_sets import per_player_target_set_template
    per_player=per_player_target_set_template(text)
    if per_player is not None:return per_player
    from .compound_target_sets import compound_target_set_effect_template
    if compound := compound_target_set_effect_template(text):return compound
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
    characteristic = _temporary_characteristic_resolution_template(text,card_name=card_name,
        source_is_permanent=source_is_permanent,source_card_types=source_card_types)
    if characteristic is not None:return characteristic
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
