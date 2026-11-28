from __future__ import annotations
from ..bounded_hand_cast_model import bounded_hand_cast_node_capabilities
from .ripple import ripple_node_capabilities
from .demonstrate import demonstrate_node_capabilities
from .gravestorm import gravestorm_node_capabilities
from .control_capability_shapes import resolution_control_node_capabilities
from .graveyard_return_choices import graveyard_return_choice_node_capabilities
from .targeted_graveyard_exile import targeted_graveyard_exile_node_capabilities
from .all_kind_counter_shapes import all_kind_counter_component_capabilities
from .next_spell_payment import temporary_player_rule_node_capabilities
from .extra_turn_shapes import fixed_extra_turn_node_capabilities
from .scheduled_turn_skip_shapes import scheduled_turn_skip_node_capabilities
from .combat_removal_shapes import combat_removal_node_capabilities
from .earthbend_shapes import earthbend_node_capabilities
from .suspect_shapes import suspect_node_capabilities
from .resolution_characteristic_shapes import fixed_resolution_characteristic_capabilities

"""Capability closure for generic two-clause effect sequences."""

from typing import Any, Iterable, Mapping, Sequence
from .untargeted_untap_shapes import fixed_land_untap_choice_capabilities
from .library_order_shapes import fixed_library_order_capabilities
from .target_library_inspection import target_library_inspection_node_capabilities
from .graveyard_recycling import graveyard_recycling_node_capabilities
from .target_land_type import target_land_type_node_capabilities
from .source_sacrifice_shapes import fixed_source_sacrifice_node_capabilities
from .chosen_counter_removal import chosen_counter_removal_node_capabilities
from .delayed_draw_capability_shapes import fixed_next_turn_draw_node_capabilities

from .affected_player_sacrifice_capability_shapes import (
    fixed_affected_player_sacrifice_node_capabilities,
)
from .whole_hand_discard_shapes import whole_hand_discard_node_capabilities
from .discard_result_shapes import discard_result_node_capabilities
from .fixed_rule_action_coverage import fixed_day_night_designation_capabilities
from ..random_discard import random_discard_node_capabilities
from .affected_player_discard_capability_shapes import (
    fixed_affected_player_discard_node_capabilities,
    fixed_affected_player_hand_exile_node_capabilities,
    fixed_controller_discard_node_capabilities,
)
from .counter_capability_shapes import (
    fixed_counter_placement_group_node_capabilities,
)
from .creature_power_damage_capability_shapes import (
    fixed_creature_power_damage_node_capabilities,
)
from .counter_removal_capabilities import (
    all_counter_removal_node_capabilities,
    fixed_counter_removal_node_capabilities,
)
from .fixed_controller_effect_shapes import fixed_life_node_capabilities
from .fixed_resolution_mana_shapes import fixed_resolution_mana_choice_capabilities, fixed_resolution_mana_capabilities
from .stack_spell_copy_shapes import fixed_stack_spell_copy_node_capabilities
from .created_token_counter_shapes import created_token_counter_node_capabilities
from .incubate_capability_shapes import incubate_node_capabilities
from .endure_shapes import fixed_endure_node_capabilities
from .blight_shapes import fixed_blight_node_capabilities
from .discover_capability_shapes import discover_node_capabilities
from .detain import detain_node_capabilities, direct_action_prohibition_node_capabilities
from .variable_library_selection_shapes import variable_library_selection_node_capabilities
from .reveal_until_library_selection_shapes import reveal_until_library_selection_node_capabilities
from .linked_exile_return_shapes import linked_exile_return_node_capabilities
from .fixed_resolution_characteristic_shapes import (
    fixed_resolution_characteristic_set_node_capabilities,
)
from .prevention_component_shapes import fixed_prevention_component_capabilities
from .graveyard_card_targets import (
    targeted_graveyard_return_node_capabilities,
)
from .node_capability_shapes import (
    fixed_amass_node_capabilities,
    fixed_bolster_node_capabilities,
    fixed_counter_placement_batch_node_capabilities,
    fixed_counter_placement_node_capabilities,
    fixed_counter_placement_set_node_capabilities,
    fixed_counter_placement_target_set_node_capabilities,
    fixed_damage_node_capabilities,
    fixed_draw_node_capabilities,
    fixed_player_counter_placement_node_capabilities,
    fixed_scry_node_capabilities,
    fixed_self_counter_keyword_action_node_capabilities,
    fixed_target_characteristics_node_capabilities,
    mass_destruction_node_capabilities,
    self_regeneration_node_capabilities,
    single_explore_node_capabilities,
    single_proliferate_node_capabilities,
    targeted_counter_node_capabilities,
    targeted_destruction_node_capabilities,
    targeted_exile_node_capabilities,
    targeted_return_to_hand_node_capabilities,
    targeted_tap_state_node_capabilities,
    temporary_declaration_restriction_node_capabilities,
)
from .goad_capability_shapes import fixed_goad_node_capabilities
from .fixed_homogeneous_target_set_capability_shapes import (
    fixed_homogeneous_target_set_node_capabilities,
    announced_target_set_node_capabilities,
)
from .library_search_capability_shapes import (
    fixed_library_search_node_capabilities,
    fixed_type_to_hand_search_node_capabilities,
)
from .mill_capability_shapes import fixed_mill_node_capabilities
from .fractional_player_result_shapes import (
    half_choice_node_capabilities, half_library_draw_node_capabilities, half_life_node_capabilities,
)
from .third_life_shapes import third_life_node_capabilities
from .public_zone_move_capability_shapes import (
    fixed_public_zone_move_set_node_capabilities,
    public_graveyard_card_exile_node_capabilities,
)
from .token_creation_capability_shapes import (
    fixed_token_creation_node_capabilities,
)
from .surveil_capability_shapes import fixed_surveil_node_capabilities
from .attachment_action_capability_shapes import fixed_attachment_action_node_capabilities
from .controlled_return import controlled_return_node_capabilities
from .entry_return_capability_shapes import fixed_entry_return_node_capabilities
from .tap_state_event_capability_shapes import tap_state_event_player_node_capabilities
from .impulse_access_capability_shapes import fixed_impulse_access_node_capabilities
from .monarch_capability_shapes import fixed_monarch_node_capabilities
from ..initiative_model import dungeon_choice_node_capabilities
from .ring_capability_shapes import ring_temptation_node_capabilities
from .event_player_choice_shapes import event_player_sacrifice_node_capabilities
from .transform_capability_shapes import source_transform_node_capabilities
from .subtype_transform import subtype_transform_node_capabilities
from ..preparation_model import preparation_result_node_capabilities
from .library_selection_capability_shapes import fixed_library_selection_node_capabilities
from .hand_inspection_capability_shapes import fixed_hand_inspection_node_capabilities
from .reanimation_capability_shapes import fixed_target_reanimation_node_capabilities
from .public_zone_move_capability_shapes import (
    fixed_owner_zone_move_node_capabilities, triggered_source_exile_node_capabilities,
)
from .node_capability_shapes import (
    fixed_source_characteristics_node_capabilities,
    fixed_target_effect_sequence_node_capabilities,
    fixed_source_effect_sequence_node_capabilities,
)
from .self_return_capability_shapes import fixed_self_return_node_capabilities
from .graveyard_self_return import graveyard_self_return_node_capabilities
from .temporary_target_interaction_shapes import temporary_target_interaction_node_capabilities
from ..replacement.face_status_copy import source_refacing_component_capabilities
from .color_retaining_copy import retained_color_copy_component_capabilities
from ..compiler.optional_effect_templates import (
    FIXED_OPTIONAL_EFFECT_CAPABILITY,
    FIXED_OPTIONAL_EFFECT_MECHANIC,
    OPTIONAL_EFFECT_OPERATION,
)
from ..compiler.optional_payment_templates import (
    FIXED_OPTIONAL_MANA_PAYMENT_CAPABILITY,
    FIXED_OPTIONAL_MANA_PAYMENT_MECHANIC,
    OPTIONAL_MANA_PAYMENT_OPERATION,
)


FIXED_EFFECT_CLAUSE_SEQUENCE_MECHANIC = "fixed-effect-clause-sequence"
FIXED_EFFECT_CLAUSE_SEQUENCE_CAPABILITY = (
    "resolution.effect_sequence.fixed_clauses"
)

_COMPONENT_RESOLVERS = (
    gravestorm_node_capabilities,
    demonstrate_node_capabilities,
    ripple_node_capabilities,
    bounded_hand_cast_node_capabilities,
    fixed_source_sacrifice_node_capabilities,
    chosen_counter_removal_node_capabilities,
    target_land_type_node_capabilities,
    graveyard_recycling_node_capabilities,
    target_library_inspection_node_capabilities,
    fixed_day_night_designation_capabilities,
    preparation_result_node_capabilities,
    graveyard_return_choice_node_capabilities,
    targeted_graveyard_exile_node_capabilities,
    fixed_land_untap_choice_capabilities,
    fixed_library_order_capabilities,
    fixed_stack_spell_copy_node_capabilities,
    created_token_counter_node_capabilities,
    incubate_node_capabilities,
    fixed_endure_node_capabilities,
    fixed_blight_node_capabilities,
    discover_node_capabilities,
    detain_node_capabilities,
    direct_action_prohibition_node_capabilities,
    variable_library_selection_node_capabilities,
    reveal_until_library_selection_node_capabilities,
    fixed_resolution_mana_choice_capabilities,
    fixed_resolution_mana_capabilities,
    all_kind_counter_component_capabilities,
    temporary_player_rule_node_capabilities,
    fixed_goad_node_capabilities,
    fixed_prevention_component_capabilities,
    fixed_extra_turn_node_capabilities,
    scheduled_turn_skip_node_capabilities,
    combat_removal_node_capabilities,
    earthbend_node_capabilities,
    suspect_node_capabilities,
    fixed_resolution_characteristic_capabilities,
    whole_hand_discard_node_capabilities,
    discard_result_node_capabilities,
    random_discard_node_capabilities,
    resolution_control_node_capabilities,
    fixed_attachment_action_node_capabilities,
    fixed_source_characteristics_node_capabilities,
    fixed_target_effect_sequence_node_capabilities,
    fixed_source_effect_sequence_node_capabilities,
    fixed_entry_return_node_capabilities,
    controlled_return_node_capabilities,
    tap_state_event_player_node_capabilities,
    fixed_impulse_access_node_capabilities,
    fixed_monarch_node_capabilities,
    dungeon_choice_node_capabilities,
    ring_temptation_node_capabilities,
    event_player_sacrifice_node_capabilities,
    source_transform_node_capabilities,
    source_refacing_component_capabilities,
    retained_color_copy_component_capabilities,
    subtype_transform_node_capabilities,
    fixed_library_selection_node_capabilities,
    fixed_hand_inspection_node_capabilities,
    fixed_target_reanimation_node_capabilities,
    fixed_owner_zone_move_node_capabilities,
    linked_exile_return_node_capabilities,
    all_counter_removal_node_capabilities,
    fixed_controller_discard_node_capabilities,
    fixed_affected_player_discard_node_capabilities,
    fixed_affected_player_hand_exile_node_capabilities,
    fixed_affected_player_sacrifice_node_capabilities,
    half_choice_node_capabilities,
    half_library_draw_node_capabilities,
    half_life_node_capabilities,
    third_life_node_capabilities,
    fixed_counter_placement_batch_node_capabilities,
    fixed_counter_placement_group_node_capabilities,
    fixed_counter_placement_node_capabilities,
    fixed_counter_placement_set_node_capabilities,
    fixed_counter_placement_target_set_node_capabilities,
    fixed_homogeneous_target_set_node_capabilities,
    announced_target_set_node_capabilities,
    fixed_counter_removal_node_capabilities,
    fixed_player_counter_placement_node_capabilities,
    fixed_target_characteristics_node_capabilities,
    temporary_target_interaction_node_capabilities,
    fixed_creature_power_damage_node_capabilities,
    temporary_declaration_restriction_node_capabilities,
    fixed_damage_node_capabilities,
    fixed_resolution_characteristic_set_node_capabilities,
    mass_destruction_node_capabilities,
    fixed_draw_node_capabilities,
    fixed_life_node_capabilities,
    fixed_scry_node_capabilities,
    fixed_surveil_node_capabilities,
    fixed_self_return_node_capabilities,
    graveyard_self_return_node_capabilities,
    triggered_source_exile_node_capabilities,
    fixed_mill_node_capabilities,
    fixed_library_search_node_capabilities,
    fixed_type_to_hand_search_node_capabilities,
    single_explore_node_capabilities,
    single_proliferate_node_capabilities,
    self_regeneration_node_capabilities,
    fixed_self_counter_keyword_action_node_capabilities,
    fixed_bolster_node_capabilities,
    fixed_amass_node_capabilities,
    targeted_counter_node_capabilities,
    targeted_destruction_node_capabilities,
    targeted_exile_node_capabilities,
    targeted_graveyard_return_node_capabilities,
    targeted_return_to_hand_node_capabilities,
    targeted_tap_state_node_capabilities,
    public_graveyard_card_exile_node_capabilities,
    fixed_public_zone_move_set_node_capabilities,
    fixed_token_creation_node_capabilities,
)


def _contains_target_reference(value: Any) -> bool:
    if isinstance(value,Mapping) and 'fixed_target_role_damage' in value:return True
    if isinstance(value, str):
        return (
            value in {"$target", "$targets"}
            or value.startswith("$target.")
        )
    if isinstance(value, Mapping):
        if value.get('op') == 'create_token' and isinstance(value.get('copy_spec'), Mapping):
            from ..token_copy_recipes import TokenCopyRecipeSpec
            try:
                if TokenCopyRecipeSpec.from_dict(value['copy_spec']).origin == 'target':
                    return True
            except (TypeError, ValueError):
                pass
        return any(_contains_target_reference(child) for child in value.values())
    if isinstance(value, (list, tuple)):
        return any(_contains_target_reference(child) for child in value)
    return False


def closed_effect_component_capabilities(
    effects: Sequence[Mapping[str, Any]],
    *,
    target_schema: Mapping[str, Any] | None,
    mechanics: set[str],
) -> tuple[str, ...]:
    from ..extra_combat_model import extra_combat_node_capabilities, uses_paid_attacking_combat
    from .land_play_permissions import TURN_LAND_PLAY_ALLOWANCE_FIELD, turn_land_plays_node_capabilities

    if len(effects) == 1 and uses_paid_attacking_combat(effects[0]):
        return extra_combat_node_capabilities(effects=effects, target_schema=target_schema, mechanic_ids=mechanics)
    if len(effects) == 1 and TURN_LAND_PLAY_ALLOWANCE_FIELD in effects[0]:
        return turn_land_plays_node_capabilities(
            effects=effects, target_schema=target_schema, mechanic_ids=mechanics,
        )
    if (len(effects) == 1 and effects[0].get("op") == "copy_all_tokens"
            and "schema_version" in effects[0]):
        from ..token_copy_recipes import OPTIONAL_CONTROLLED_TOKEN_COPY_CAPABILITY, validate_optional_controlled_token_copy

        if target_schema is not None or "cr-111-tokens" not in mechanics:
            return ()
        try:
            validate_optional_controlled_token_copy(effects[0])
        except (TypeError, ValueError):
            return ()
        return (OPTIONAL_CONTROLLED_TOKEN_COPY_CAPABILITY,)
    from ..delayed_triggers import (
        FIXED_NEXT_UPKEEP_PAYMENT_CAPABILITY, fixed_next_upkeep_payment_cost,
    )

    if ("cr-603-handling-triggered-abilities" in mechanics and target_schema is None and len(effects) == 1
            and effects[0].get("op") == "delayed_trigger" and fixed_next_upkeep_payment_cost(effects[0]) is not None):
        return (FIXED_NEXT_UPKEEP_PAYMENT_CAPABILITY, "trigger.placement.apnap")

    optional_payment = fixed_optional_mana_payment_node_capabilities(
        effects=effects,
        target_schema=target_schema,
        mechanic_ids=mechanics,
    )
    if optional_payment:
        return optional_payment
    optional = fixed_optional_effect_node_capabilities(
        effects=effects,
        target_schema=target_schema,
        mechanic_ids=mechanics,
    )
    if optional:
        return optional
    from .target_player_sequences import target_player_sequence_node_capabilities
    from .target_set_result_sequences import target_set_result_sequence_capabilities
    for resolver in (target_player_sequence_node_capabilities,target_set_result_sequence_capabilities):
        owned=resolver(effects=effects,target_schema=target_schema,mechanic_ids=mechanics)
        if owned:return owned
    dependencies: set[str] = set()
    for resolver in _COMPONENT_RESOLVERS:
        component_mechanics = mechanics
        if resolver is fixed_token_creation_node_capabilities:
            from .token_creation_capability_shapes import token_creation_component_mechanics
            component_mechanics = token_creation_component_mechanics(effects, mechanics)
        elif resolver is self_regeneration_node_capabilities:
            component_mechanics = {
                "regenerate",
                *(("cr-115-targets",) if target_schema is not None else ()),
            }
        dependencies.update(
            resolver(
                effects=effects,
                target_schema=target_schema,
                mechanic_ids=component_mechanics,
            )
        )
    return tuple(sorted(dependencies))


def fixed_optional_mana_payment_node_capabilities(
    *,
    effects: Sequence[Mapping[str, Any]],
    target_schema: Mapping[str, Any] | None,
    mechanic_ids: Iterable[str],
) -> tuple[str, ...]:
    """Recognize one positive fixed-mana wrapper around one closed effect."""

    if any(effect.get('schema_version') == 13 and effect.get('op') == OPTIONAL_MANA_PAYMENT_OPERATION for effect in effects):
        from .optional_sacrifice_stats import optional_sacrifice_stat_node_capabilities
        return optional_sacrifice_stat_node_capabilities(effects=effects, target_schema=target_schema)

    from .life_gain_counted_results import (
        capped_life_gain_draw_node_capabilities,
        life_gain_life_draw_node_capabilities,
        uses_capped_life_gain_draw,
        uses_life_gain_life_draw,
    )
    from .entrant_x_reflexive import entrant_x_node_capabilities, uses_entrant_x_payment

    if any(uses_life_gain_life_draw(effect) for effect in effects):
        return life_gain_life_draw_node_capabilities(effects=effects, target_schema=target_schema)
    if any(uses_entrant_x_payment(effect) for effect in effects):
        return entrant_x_node_capabilities(effects=effects, target_schema=target_schema)
    if any(uses_capped_life_gain_draw(effect) for effect in effects):
        return capped_life_gain_draw_node_capabilities(effects=effects, target_schema=target_schema)
    if any('life_gain_counter_result_version' in effect for effect in effects):
        from .life_gain_counted_results import life_gain_counter_payment_node_capabilities
        return life_gain_counter_payment_node_capabilities(effects=effects, target_schema=target_schema)

    if any(effect.get('schema_version') == 5 and effect.get('op') == OPTIONAL_MANA_PAYMENT_OPERATION for effect in effects):
        from .commander_learn_shapes import commander_learn_node_capabilities
        return commander_learn_node_capabilities(effects=effects, target_schema=target_schema, mechanic_ids=mechanic_ids)
    from .nonmana_activation_copy import marks_activation_copy_payment, nonmana_activation_copy_node_dependencies

    if any(marks_activation_copy_payment(effect) for effect in effects):
        return nonmana_activation_copy_node_dependencies(effects, target_schema)
    from ..fixed_effect_payment import MANDATORY_SOURCE_COUNTER_FOLLOWUP_VERSION
    from ..spell_caster_sacrifice_payment import SPELL_CASTER_SACRIFICE_PAYMENT_VERSION

    from .attached_copy_payment import VERSION as ATTACHED_COPY_PAYMENT_VERSION

    fixed_payment_versions = (
        2, 3, 4, 6, 7, 8, 9, 10, 11, SPELL_CASTER_SACRIFICE_PAYMENT_VERSION,
        MANDATORY_SOURCE_COUNTER_FOLLOWUP_VERSION, ATTACHED_COPY_PAYMENT_VERSION,
    )
    if any(
        effect.get("schema_version") in fixed_payment_versions
        and effect.get("op") == OPTIONAL_MANA_PAYMENT_OPERATION
        for effect in effects
    ):
        from .fixed_effect_payment_shapes import fixed_effect_payment_node_capabilities
        return fixed_effect_payment_node_capabilities(effects=effects,target_schema=target_schema,mechanic_ids=mechanic_ids)

    mechanics = {str(value).casefold() for value in mechanic_ids}
    if (
        FIXED_OPTIONAL_MANA_PAYMENT_MECHANIC not in mechanics
        or len(effects) != 1
    ):
        return ()
    wrapper = effects[0]
    if (
        set(wrapper) != {"op", "player", "cost", "effects"}
        or wrapper.get("op") != OPTIONAL_MANA_PAYMENT_OPERATION
        or wrapper.get("player") != "$controller"
    ):
        return ()
    cost = wrapper.get("cost")
    mana_keys = {"GENERIC", "W", "U", "B", "R", "G", "C"}
    if (
        not isinstance(cost, Mapping)
        or set(cost) != mana_keys
        or any(type(amount) is not int or amount < 0 for amount in cost.values())
        or not any(cost.values())
    ):
        return ()
    nested = wrapper.get("effects")
    if (
        not isinstance(nested, (list, tuple))
        or len(nested) != 1
        or not isinstance(nested[0], Mapping)
        or nested[0].get("op")
        in {OPTIONAL_EFFECT_OPERATION, OPTIONAL_MANA_PAYMENT_OPERATION}
    ):
        return ()
    references_target = _contains_target_reference(nested[0])
    if (target_schema is None) == references_target:
        return ()
    dependencies = closed_effect_component_capabilities(
        (nested[0],),
        target_schema=target_schema,
        mechanics=mechanics - {FIXED_OPTIONAL_MANA_PAYMENT_MECHANIC},
    )
    if not dependencies:
        return ()
    return tuple(
        sorted({FIXED_OPTIONAL_MANA_PAYMENT_CAPABILITY, *dependencies})
    )


def is_closed_optional_composed_effect_program(program):
    from .as_unblocked_effect import is_closed_optional_as_unblocked_program
    from ..replacement.face_status_copy import is_closed_upkeep_refacing_program
    from .color_retaining_copy import is_closed_retained_color_upkeep_program
    from .persistent_self_copy_trigger import is_closed_persistent_self_copy_trigger
    from .player_life_exchange import is_closed_optional_player_life_exchange_program

    return (is_closed_optional_as_unblocked_program(program) or is_closed_upkeep_refacing_program(program)
            or is_closed_retained_color_upkeep_program(program) or is_closed_persistent_self_copy_trigger(program)
            or is_closed_optional_player_life_exchange_program(program))


def _versioned_optional_wrapper_is_closed(wrapper, target_schema, mechanics):
    from .optional_draw_discard import is_entry_exempt_draw_discard, validate_entry_exempt_draw_discard
    from .fixed_homogeneous_target_set_capability_shapes import validate_optional_graveyard_target_return
    from .transform_capability_shapes import (
        OPTIONAL_SOURCE_TRANSFORM_VERSION, TRIGGERED_SOURCE_TRANSFORM_MECHANIC,
        optional_source_transform_child,
    )

    try:
        if is_entry_exempt_draw_discard(wrapper):
            validate_entry_exempt_draw_discard(wrapper)
            return target_schema is None
        if wrapper["schema_version"] == OPTIONAL_SOURCE_TRANSFORM_VERSION:
            optional_source_transform_child(wrapper, player="$controller")
            return target_schema is None and TRIGGERED_SOURCE_TRANSFORM_MECHANIC in mechanics
        validate_optional_graveyard_target_return(wrapper, player="$controller")
    except (TypeError, ValueError):
        return False
    return (isinstance(target_schema, Mapping) and type(target_schema.get("up_to")) is int
            and target_schema["up_to"] == 1)


def fixed_optional_effect_node_capabilities(
    *,
    effects: Sequence[Mapping[str, Any]],
    target_schema: Mapping[str, Any] | None,
    mechanic_ids: Iterable[str],
) -> tuple[str, ...]:
    """Recognize one optional wrapper around one closed atomic effect."""

    mechanics = {str(value).casefold() for value in mechanic_ids}
    if FIXED_OPTIONAL_EFFECT_MECHANIC not in mechanics or len(effects) != 1:
        return ()
    from .searched_library_pairs import searched_pair_node_capabilities

    pair = searched_pair_node_capabilities(effects=effects, target_schema=target_schema, mechanic_ids=mechanics)
    if pair:
        return pair
    wrapper = effects[0]
    from .optional_draw_discard import is_entry_exempt_draw_discard
    from ..public_history_query_model import PUBLIC_HISTORY_QUERY_CAPABILITY
    history = (PUBLIC_HISTORY_QUERY_CAPABILITY,) if is_entry_exempt_draw_discard(wrapper) else ()
    from .entry_creature_type_mana import (
        is_optional_entry_shared_type_mana, optional_entry_shared_type_mana_node_capabilities,
    )

    from ..linked_library_imprint_model import uses_linked_artifact_library_choice
    if uses_linked_artifact_library_choice(wrapper):
        from .linked_library_imprint import linked_artifact_library_node_capabilities

        return linked_artifact_library_node_capabilities(effects=effects, target_schema=target_schema)
    if is_optional_entry_shared_type_mana(wrapper):
        return optional_entry_shared_type_mana_node_capabilities(effects, target_schema)
    from .landfall_life_loss_counters import uses_optional_loss_source_counters, optional_loss_source_counter_node_capabilities

    if uses_optional_loss_source_counters(wrapper):
        return optional_loss_source_counter_node_capabilities(effects=effects, target_schema=target_schema)
    from .optional_targeted_control import is_optional_targeted_control, optional_targeted_control_node_capabilities

    if is_optional_targeted_control(wrapper):
        return optional_targeted_control_node_capabilities(effects=effects, target_schema=target_schema)
    from .event_player_result_shapes import fixed_event_player_damage_node_capabilities, is_optional_drawing_player_damage

    if is_optional_drawing_player_damage(wrapper):
        damage = fixed_event_player_damage_node_capabilities(
            effects=wrapper["effects"], target_schema=target_schema, mechanic_ids=mechanics,
        )
        return tuple(sorted({FIXED_OPTIONAL_EFFECT_CAPABILITY, *damage})) if damage else ()
    if "schema_version" in wrapper:
        if not _versioned_optional_wrapper_is_closed(wrapper, target_schema, mechanics):
            return ()
        wrapper = {key: value for key, value in wrapper.items() if key != "schema_version"}
    if (
        set(wrapper) != {"op", "player", "effects"}
        or wrapper.get("op") != OPTIONAL_EFFECT_OPERATION
        or wrapper.get("player") != "$controller"
    ):
        return ()
    nested = wrapper.get("effects")
    linked = (
        linked_exile_return_node_capabilities(
            effects=nested, target_schema=target_schema,
            mechanic_ids=mechanics - {FIXED_OPTIONAL_EFFECT_MECHANIC},
        )
        if isinstance(nested, (list, tuple)) and all(isinstance(value, Mapping) for value in nested)
        else ()
    )
    if linked:
        return tuple(sorted({FIXED_OPTIONAL_EFFECT_CAPABILITY, *linked}))
    from ..permanent_event_state_model import (
        COUNTERED_COMBAT_DRAW_PROLIFERATE_CAPABILITY, optional_draw_proliferate_is_closed,
    )
    if target_schema is None and optional_draw_proliferate_is_closed(nested):
        components = tuple(closed_effect_component_capabilities(
            (child,), target_schema=None, mechanics=mechanics - {FIXED_OPTIONAL_EFFECT_MECHANIC},
        ) for child in nested)
        if all(components):
            return tuple(sorted({FIXED_OPTIONAL_EFFECT_CAPABILITY, COUNTERED_COMBAT_DRAW_PROLIFERATE_CAPABILITY,
                                 *(cap for owned in components for cap in owned)}))
        return ()
    from .optional_draw_discard import optional_draw_discard_is_closed
    if target_schema is None and optional_draw_discard_is_closed(nested,player='$controller'):
        from ..compiler.affected_player_discard_templates import FIXED_CONTROLLER_DISCARD_MECHANIC
        discard = fixed_controller_discard_node_capabilities(effects=(nested[1],),target_schema=None,mechanic_ids=mechanics) if FIXED_CONTROLLER_DISCARD_MECHANIC in mechanics else fixed_affected_player_discard_node_capabilities(effects=(nested[1],),target_schema=None,mechanic_ids=mechanics,allow_controller=True)
        components=(closed_effect_component_capabilities((nested[0],),target_schema=None,mechanics=mechanics-{FIXED_OPTIONAL_EFFECT_MECHANIC}),
            discard)
        return tuple(sorted({FIXED_OPTIONAL_EFFECT_CAPABILITY, *history,
                             *(cap for caps in components for cap in caps)})) if all(components) else ()
    if (
        not isinstance(nested, (list, tuple))
        or len(nested) != 1
        or not isinstance(nested[0], Mapping)
        or nested[0].get("op") == OPTIONAL_EFFECT_OPERATION
    ):
        return ()
    from ..named_search_model import LITERAL_NAME_BATTLEFIELD_SEARCH_VERSION
    if (nested[0].get("op") == "search"
            and nested[0].get("schema_version") == LITERAL_NAME_BATTLEFIELD_SEARCH_VERSION):
        return ()
    from .attachment_actions import is_targeted_equipment_attachment

    references_target = _contains_target_reference(nested[0]) or is_targeted_equipment_attachment(nested[0])
    if (target_schema is None) == references_target:
        return ()
    dependencies = closed_effect_component_capabilities(
        (nested[0],),
        target_schema=target_schema,
        mechanics=mechanics - {FIXED_OPTIONAL_EFFECT_MECHANIC},
    )
    if not dependencies:
        return ()
    return tuple(
        sorted({FIXED_OPTIONAL_EFFECT_CAPABILITY, *dependencies})
    )


def _shared_damage_attack_dependencies(effects, target_schema, mechanics):
    from .temporary_attack_requirements import temporary_attack_requirement_node_capabilities
    from ..temporary_attack_requirement_model import TemporaryAttackRequirementSpec

    if FIXED_EFFECT_CLAUSE_SEQUENCE_MECHANIC not in mechanics:
        return ()
    damage, requirement = effects
    if (set(damage) != {"op", "source", "target", "amount"}
            or damage["op"] != "damage" or damage["source"] != "$source" or damage["target"] != "$target.0"
            or type(damage["amount"]) is not int or damage["amount"] <= 0
            or requirement.get("restriction") != TemporaryAttackRequirementSpec().to_dict()):
        return ()
    dealt = fixed_damage_node_capabilities(effects=(damage,), target_schema=target_schema, mechanic_ids=mechanics)
    attack = temporary_attack_requirement_node_capabilities(
        effects=(requirement,), target_schema=target_schema, mechanic_ids=mechanics,
    )
    if not dealt or not attack:
        return ()
    return tuple(sorted({FIXED_EFFECT_CLAUSE_SEQUENCE_CAPABILITY, *dealt, *attack}))


def shared_damage_attack_runtime_dependencies(program):
    from ..compiler.fixed_effect_clause_sequences import DAMAGE_ATTACK_REQUIREMENT_TEMPLATE

    effects = program.effects
    template = program.provenance.get("template_id")
    if template != DAMAGE_ATTACK_REQUIREMENT_TEMPLATE:
        if len(effects) == 2 and _shared_damage_attack_dependencies(effects, program.target_schema, program.coverage):
            raise ValueError("Shared damage and attack requirement needs its exact sequence template")
        return ()
    if len(effects) != 2 or (program.event, program.active_zone) != ("activate", "battlefield"):
        raise ValueError("Shared damage and attack requirement needs its original activated source")
    required = _shared_damage_attack_dependencies(program.effects, program.target_schema, program.coverage)
    if not required:
        raise ValueError("Shared damage and attack requirement lost its exact ordered creature target")
    return required


def _uses_controlled_creature_counter_choice(effects):
    from ..counter_placement_targets import CONTROLLED_CREATURE_COUNTER_CHOICE_FIELD

    return any(effect.get(CONTROLLED_CREATURE_COUNTER_CHOICE_FIELD) == 1 for effect in effects)


def _controlled_counter_proliferate_capabilities(effects, target_schema, mechanics):
    from ..counter_placement_targets import (
        CONTROLLED_CREATURE_COUNTER_CHOICE_FIELD, controlled_creature_counter_choice_dependencies,
    )
    from ..counter_placement_event_model import PROLIFERATE_ORDER_VERSION_FIELD

    first, second = effects
    if (target_schema is not None or "cr-122-counters" not in mechanics
            or first.get(CONTROLLED_CREATURE_COUNTER_CHOICE_FIELD) != 1
            or type(second.get(PROLIFERATE_ORDER_VERSION_FIELD)) is not int
            or second.get(PROLIFERATE_ORDER_VERSION_FIELD) != 1):
        return ()
    try:
        selected = controlled_creature_counter_choice_dependencies(first)
    except (TypeError, ValueError):
        return ()
    proliferated = single_proliferate_node_capabilities(
        effects=(second,), target_schema=None, mechanic_ids=mechanics,
    )
    return (FIXED_EFFECT_CLAUSE_SEQUENCE_CAPABILITY, *selected, *proliferated) if selected and proliferated else ()


def equipped_counter_proliferate_runtime_dependencies(program):
    if not _uses_controlled_creature_counter_choice(program.effects):
        return ()
    from ..replacement.immutable import FrozenMap

    equipped_hit = {"all": [
        {"field": "source_attachment_target_ref", "op": "eq", "value": "$context.source"},
        {"field": "target_kind", "op": "eq", "value": "player"},
        {"field": "combat", "op": "truthy", "value": True},
    ]}
    owned = fixed_effect_clause_sequence_node_capabilities(
        effects=program.effects, target_schema=program.target_schema, mechanic_ids=program.coverage,
    )
    if (not owned or (program.event, program.active_zone) != ("damage.dealt", "battlefield")
            or FrozenMap(program.event_condition or {}) != FrozenMap(equipped_hit)
            or program.cost_schema is not None or program.handlers):
        raise ValueError("A controlled creature counter then Proliferate needs its original equipped combat occurrence")
    return (*owned, "attachment.reference.current_or_lki", "trigger.event.normalized_damage",
            "trigger.placement.apnap")


def fixed_effect_clause_sequence_node_capabilities(
    *,
    effects: Sequence[Mapping[str, Any]],
    target_schema: Mapping[str, Any] | None,
    mechanic_ids: Iterable[str],
) -> tuple[str, ...]:
    """Own two independently closed effects in their printed order."""

    mechanics = {str(value).casefold() for value in mechanic_ids}
    if (
        FIXED_EFFECT_CLAUSE_SEQUENCE_MECHANIC not in mechanics
        or len(effects) != 2
    ):
        return ()
    if _uses_controlled_creature_counter_choice(effects):
        return _controlled_counter_proliferate_capabilities(effects, target_schema, mechanics)
    if effects[0].get("op") == "lose_life" and effects[1].get("ability_fragments"):
        from .temporary_ability_grant import life_loss_temporary_trigger_capabilities

        children = life_loss_temporary_trigger_capabilities(effects, target_schema, mechanics)
        if children:
            return (FIXED_EFFECT_CLAUSE_SEQUENCE_CAPABILITY, *children)
    target_components = tuple(
        index
        for index, effect in enumerate(effects)
        if _contains_target_reference(effect)
    )
    if target_schema is None:
        if target_components:
            return ()
    elif target_components == (0, 1):
        return _shared_damage_attack_dependencies(effects, target_schema, mechanics)
    elif len(target_components) != 1:
        return ()

    dependencies = {FIXED_EFFECT_CLAUSE_SEQUENCE_CAPABILITY}
    for index, effect in enumerate(effects):
        component = closed_effect_component_capabilities(
            (effect,),
            target_schema=(
                target_schema if index in target_components else None
            ),
            mechanics=mechanics - {FIXED_EFFECT_CLAUSE_SEQUENCE_MECHANIC},
        )
        if not component and index == 1 and target_land_type_node_capabilities(
            effects=(effects[0],), target_schema=target_schema, mechanic_ids=mechanics
        ):
            component = fixed_next_turn_draw_node_capabilities(
                effects=(effect,), target_schema=None, mechanic_ids=mechanics
            )
        if not component:
            return ()
        dependencies.update(component)
    return tuple(sorted(dependencies))


def fixed_proliferate_sequence_runtime_dependencies(program) -> tuple[str, ...]:
    from ..compiler.fixed_effect_clause_sequences import FIXED_PROLIFERATE_SEQUENCE_TEMPLATE
    provenance = getattr(program, "provenance", {})
    triggered = FIXED_PROLIFERATE_SEQUENCE_TEMPLATE in getattr(program, "coverage", ())
    if not triggered and provenance.get("template_id") != FIXED_PROLIFERATE_SEQUENCE_TEMPLATE:
        return ()
    from ..counter_placement_event_model import has_canonical_proliferate_choice_order
    from ..semantic_runtime.activated_abilities import ACTIVATED_ABILITY_CATALOG_HANDLER_ID

    if (program.target_schema is not None or len(program.effects) != 2
            or not all(has_canonical_proliferate_choice_order(effect) for effect in program.effects)):
        raise ValueError("Fixed Proliferate sequence requires two ordinary, untargeted versioned instructions")
    if triggered:
        from .event_subscriptions import FixedEventSubscription, FixedEventSubscriptionSet

        source = {"field": "card", "op": "eq", "value": "$source.ref"}
        expected = FixedEventSubscriptionSet(tuple(
            FixedEventSubscription(event, source) for event in ("permanent.enter", "creature.attacks")
        ))
        if (program.event != "permanent.enter" or program.active_zone != "battlefield"
                or program.cost_schema is not None or program.handlers or program.trigger_limit is not None
                or FixedEventSubscriptionSet.from_condition(program.event_condition) != expected):
            raise ValueError("Triggered Proliferate twice requires exactly its own entry and attack subscriptions")
    elif (program.event != "activate" or program.cost_schema is None or len(program.handlers) != 1
          or program.handlers[0].get("handler_id") != ACTIVATED_ABILITY_CATALOG_HANDLER_ID):
        raise ValueError("Fixed Proliferate sequence requires its original activation")
    required = fixed_effect_clause_sequence_node_capabilities(
        effects=program.effects, target_schema=None, mechanic_ids=program.coverage,
    )
    if not required:
        raise ValueError("Fixed Proliferate sequence lacks complete composition authority")
    return required


def is_closed_fixed_effect_clause_sequence_program(program) -> bool:
    """Recognize an exact program of two independently closed printed clauses."""
    from ..compiler.fixed_effect_clause_sequences import (
        DAMAGE_ATTACK_REQUIREMENT_TEMPLATE, FIXED_PROLIFERATE_SEQUENCE_TEMPLATE, SOURCE_CHARACTERISTIC_TAP_TEMPLATE,
    )

    from .temporary_ability_grant import LIFE_LOSS_SELF_DEATH_GRANT_TEMPLATE

    template = program.provenance.get("template_id")
    if template in {DAMAGE_ATTACK_REQUIREMENT_TEMPLATE, FIXED_PROLIFERATE_SEQUENCE_TEMPLATE}:
        validate = (
            shared_damage_attack_runtime_dependencies if template == DAMAGE_ATTACK_REQUIREMENT_TEMPLATE
            else fixed_proliferate_sequence_runtime_dependencies
        )
        try:
            required = validate(program)
        except (TypeError, ValueError):
            return False
    elif template in {"fixed-effect-clause-sequence-v1", SOURCE_CHARACTERISTIC_TAP_TEMPLATE,
                       LIFE_LOSS_SELF_DEATH_GRANT_TEMPLATE}:
        required = fixed_effect_clause_sequence_node_capabilities(
            effects=program.effects, target_schema=program.target_schema, mechanic_ids=program.coverage,
        )
    else:
        return False
    return bool(required) and set(required).issubset(program.capability_dependencies)


def source_characteristic_tap_runtime_dependencies(program):
    """Version only the new source/tap producer; retain historical source v1 binding."""
    from ..compiler.fixed_effect_clause_sequences import SOURCE_CHARACTERISTIC_TAP_TEMPLATE
    if program.provenance.get('template_id')!=SOURCE_CHARACTERISTIC_TAP_TEMPLATE:return ()
    if (program.target_schema is not None or len(program.effects)!=2
            or program.effects[0].get('op')!='apply_source_characteristics_until_end_of_turn'
            or program.effects[1].get('op') not in {'tap','untap'}
            or any(effect.get('card')!='$source.zone_object' for effect in program.effects)):
        raise ValueError('Source characteristic/tap production lost its source and ordered result scope')
    required = fixed_effect_clause_sequence_node_capabilities(effects=program.effects,
        target_schema=None,mechanic_ids=program.coverage)
    if not required:raise ValueError('Source characteristic/tap production lacks complete leaf closure')
    from ..keyword_abilities import FIXED_CHARACTERISTIC_KEYWORD_CAPABILITIES
    return tuple(sorted({*required,*(cap for keyword in program.effects[0].get('keywords',())
        for cap in FIXED_CHARACTERISTIC_KEYWORD_CAPABILITIES[keyword])}))


__all__ = [
    "FIXED_EFFECT_CLAUSE_SEQUENCE_CAPABILITY",
    "FIXED_EFFECT_CLAUSE_SEQUENCE_MECHANIC",
    "closed_effect_component_capabilities",
    "fixed_effect_clause_sequence_node_capabilities",
    "fixed_optional_mana_payment_node_capabilities",
    "fixed_optional_effect_node_capabilities",
]
