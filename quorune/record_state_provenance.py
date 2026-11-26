from __future__ import annotations

"""Additive Game Record v3 state-semantics provenance facade."""

from typing import Any, Mapping
from .declare_attack_cast_model import declare_attack_cast_version
from .rad_counter_model import RAD_VERSION_FIELD, read_rad_trigger_version
from .mana_source_counter_payment_model import (
    LAND_MANA_TAP_VERSION_FIELD, land_mana_tap_event_fields, read_land_mana_tap_event_version,
)
from .spell_resolution_name_codec import spell_name_provenance_fields, validate_spell_name_provenance

from .cast_event_type_model import cast_event_type_snapshot_fields, read_cast_event_type_snapshot_version
from .cast_source_placement_model import cast_placement_state_fields, read_target_announcement_timing_version
from .evolve import EVOLVE_ENTRANT_LKI_VERSION_FIELD, evolve_entrant_lki_fields, evolve_entrant_lki_version
from .discard_effect_origin import (
    DISCARD_EFFECT_ORIGIN_VERSION_FIELD, discard_effect_origin_version_fields, validate_discard_effect_provenance,
)
from .model import unearth_leave_replacement_version, upkeep_completion_version
from .library_search_model import fetch_entry_placement_fields, read_fetch_entry_placement_version
from .model import (
    LAND_ENTRY_PAYMENT_VALIDATION_FIELD, land_entry_payment_validation_fields, land_entry_affordability_version,
)
from .continuous_effect_model import (
    continuous_dependency_order_fields,
    read_continuous_dependency_order_version,
)
from .damage_modifier_state import (
    SHIELD_COUNTER_DAMAGE_VERSION_FIELD,
    read_shield_counter_damage_version,
)
from .permanent_event_state_model import BATTLEFIELD_ENTRY_PLAYER_VERSION_FIELD, read_battlefield_entry_player_version
from .turn_priority_model import (
    PRIORITY_CONCESSION_STABILIZATION_VERSION_FIELD,
    read_priority_concession_stabilization_version,
)
from .damage_source import (
    DAMAGE_PREVENTION_RESULT_VERSION_FIELD,
    read_damage_prevention_result_version,
)
from .stack_resolution_authority_model import (
    COPIED_SPELL_ENTRY_AUTHORITY_VERSION,
    stack_resolution_authority_version,
    ward_discard_order_version,
)
from .mana_source_counter_payment_model import (
    mana_source_counter_payment_fields,read_mana_source_counter_payment_version,
    mana_cost_order_fields,read_mana_cost_order_version,mana_cost_order_version,
)
from .printed_mana_cost_model import (
    PRINTED_MANA_COST_VERSION_FIELD,
    printed_mana_cost_format_fields,
    read_printed_mana_cost_version,
)
from .record_commander_identity import (
    commander_damage_identity_version,
    validate_commander_damage_identity_provenance,
)
from .record_control_history import (
    serialized_control_history_version,
    validate_control_history_provenance,
)


def _combat_relationship_format_fields(state: Any) -> dict[str, int]:
    combat = getattr(state, "combat", None)
    if getattr(combat, "detached_blocker_ids", None) is None:
        return {}
    return {"combat_relationship_version": 1}


def _validate_combat_relationship_format(format_value: Mapping[str, Any], state: Any) -> None:
    from .banding_model import ninjutsu_band_retention_version

    ninjutsu_bands = format_value.get("ninjutsu_band_retention_version")
    if (
        "ninjutsu_band_retention_version" in format_value and (type(ninjutsu_bands) is not int or ninjutsu_bands != 1)
        or ninjutsu_bands != ninjutsu_band_retention_version(getattr(state, "ninjutsu_band_retention_version", None))
    ):
        raise ValueError("Ninjutsu band retention provenance does not match the initial checkpoint")
    from .banding_model import entry_attack_publication_version

    key = "attacking_entry_publication_version"
    version = entry_attack_publication_version(format_value.get(key))
    if ((key in format_value and version is None)
            or version != entry_attack_publication_version(getattr(state, key, None))):
        raise ValueError("Attacking entry publication provenance does not match the initial checkpoint")
    key = "combat_relationship_version"
    declared = format_value.get(key)
    if declared != _combat_relationship_format_fields(state).get(key) or key in format_value and type(declared) is not int:
        raise ValueError("Combat relationship provenance does not match the initial checkpoint")


def format_state_versions(state: Any) -> dict[str, int]:
    """Return canonical manifest markers for additive state semantics."""

    from .state_based_legend_model import legend_rule_choice_version
    shield_damage = getattr(state, SHIELD_COUNTER_DAMAGE_VERSION_FIELD, None)
    prevention_result = getattr(state, DAMAGE_PREVENTION_RESULT_VERSION_FIELD, None)
    land_entry_player = getattr(state, BATTLEFIELD_ENTRY_PLAYER_VERSION_FIELD, None)
    concession = getattr(state, PRIORITY_CONCESSION_STABILIZATION_VERSION_FIELD, None)
    legend = legend_rule_choice_version(getattr(state, 'legend_rule_choice_version', None))
    stack_authority = stack_resolution_authority_version(getattr(state, "stack_resolution_authority_version", None))
    departed_turns = getattr(state, "departed_turn_boundary_version", None)
    unearth = unearth_leave_replacement_version(getattr(state, "unearth_leave_replacement_version", None))
    return {
        **_combat_relationship_format_fields(state),
        **spell_name_provenance_fields(state),
        **({'declare_attack_cast_version': 1}
           if declare_attack_cast_version(getattr(state, 'declare_attack_cast_version', None)) == 1 else {}),
        **({"upkeep_completion_version": 1}
           if upkeep_completion_version(getattr(state, "upkeep_completed", None)) == 1 else {}),
        **({'legend_rule_choice_version': legend} if legend is not None else {}),
        **({"stack_resolution_authority_version": stack_authority} if stack_authority is not None else {}),
        **({"departed_turn_boundary_version": departed_turns} if departed_turns is not None else {}),
        **({"unearth_leave_replacement_version": unearth} if unearth is not None else {}),
        "commander_damage_identity_version": commander_damage_identity_version(
            state.commander_damage_identity_version
        ),
        "control_history_version": serialized_control_history_version(
            state.control_history_version
        ),
        "departure_history_version": (
            getattr(getattr(state, "turn_history", None), "departure_history_version", None) or 0
        ),
        **({"arrival_history_version":1} if getattr(getattr(state,"turn_history",None),"arrival_history_version",None)==1 else {}),
        **{key:1 for key in ('spell_origin_history_version','graveyard_type_history_version','spell_mana_history_version','spell_color_history_version','creature_spell_counter_history_version',
            'crime_history_version','committed_occurrence_history_version','damage_life_history_version',
            'permanent_death_history_version','death_name_history_version','first_life_gain_history_version','public_movement_history_version',
            'venture_history_version', 'plus_one_counter_placement_history_version',
            'land_play_history_version', 'permanent_counter_receipt_history_version', 'draw_identity_history_version')
            if getattr(getattr(state,'turn_history',None),key,None)==1},
        **({"tap_state_event_version": state.tap_state_event_version} if state.tap_state_event_version is not None else {}),
        **cast_placement_state_fields(
            getattr(state, "cast_source_placement_version", None), getattr(state, "target_announcement_timing_version", None),
        ),
        **cast_event_type_snapshot_fields(getattr(state, "cast_event_type_snapshot_version", None)),
        **({"phasing_lifecycle_version": state.phasing_lifecycle_version}
           if getattr(state, "phasing_lifecycle_version", None) is not None else {}),
        **printed_mana_cost_format_fields(getattr(state, PRINTED_MANA_COST_VERSION_FIELD, None)),
        **mana_source_counter_payment_fields(getattr(state,'mana_source_counter_payment_version',None)),
        **mana_cost_order_fields(getattr(state,'mana_cost_order_version',None)),
        **({SHIELD_COUNTER_DAMAGE_VERSION_FIELD: shield_damage} if shield_damage is not None else {}),
        **({DAMAGE_PREVENTION_RESULT_VERSION_FIELD: prevention_result} if prevention_result is not None else {}),
        **({BATTLEFIELD_ENTRY_PLAYER_VERSION_FIELD: land_entry_player} if land_entry_player is not None else {}),
        **({PRIORITY_CONCESSION_STABILIZATION_VERSION_FIELD: concession} if concession is not None else {}),
        **continuous_dependency_order_fields(getattr(state, "continuous_dependency_order_version", None)),
        **fetch_entry_placement_fields(getattr(state, "fetch_entry_placement_version", None)),
        **land_entry_payment_validation_fields(getattr(state, LAND_ENTRY_PAYMENT_VALIDATION_FIELD, None)),
        **evolve_entrant_lki_fields(getattr(state, EVOLVE_ENTRANT_LKI_VERSION_FIELD, None)),
        **discard_effect_origin_version_fields(getattr(state, DISCARD_EFFECT_ORIGIN_VERSION_FIELD, None)),
        **({'explore_action_version':state.explore_action_version} if getattr(state,'explore_action_version',None) is not None else {}),
        **({'transform_timestamp_version':state.transform_timestamp_version} if getattr(state,'transform_timestamp_version',None) is not None else {}),
        **({"modal_dfc_transform_version": state.modal_dfc_transform_version}
           if getattr(state, "modal_dfc_transform_version", None) is not None else {}),
        **({"transform_event_characteristics_version": state.transform_event_characteristics_version}
           if getattr(state, "transform_event_characteristics_version", None) is not None else {}),
        **({'face_status_timestamp_version':state.face_status_timestamp_version} if getattr(state,'face_status_timestamp_version',None) is not None else {}),
        **({"split_permanent_face_values_version": state.split_permanent_face_values_version}
           if getattr(state, "split_permanent_face_values_version", None) is not None else {}),
        **({'face_down_characteristics_version':state.face_down_characteristics_version} if getattr(state,'face_down_characteristics_version',None) is not None else {}),
        **({"face_down_departure_reveal_version": state.face_down_departure_reveal_version}
           if getattr(state, "face_down_departure_reveal_version", None) is not None else {}),
        **({'library_selection_knowledge_version':state.library_selection_knowledge_version} if getattr(state,'library_selection_knowledge_version',None) is not None else {}),
        **land_mana_tap_event_fields(getattr(state, LAND_MANA_TAP_VERSION_FIELD, None)),
        **({"saga_intrinsic_ability_version": state.saga_intrinsic_ability_version}
           if getattr(state, "saga_intrinsic_ability_version", None) is not None else {}),
        **({RAD_VERSION_FIELD: state.rad_trigger_version} if state.rad_trigger_version is not None else {}),
        **({'characteristic_query_boundary_version':state.characteristic_query_boundary_version} if getattr(state,'characteristic_query_boundary_version',None) is not None else {}),
        **({'token_copy_value_version':state.token_copy_value_version} if getattr(state,'token_copy_value_version',None) is not None else {}),
        **({"predefined_token_name_version": state.predefined_token_name_version}
           if getattr(state, "predefined_token_name_version", None) is not None else {}),
        **({'block_characteristics_version':state.block_characteristics_version} if getattr(state,'block_characteristics_version',None) is not None else {}),
        **({'unblocked_attacker_event_version':state.unblocked_attacker_event_version} if getattr(state,'unblocked_attacker_event_version',None) is not None else {}),
        **({'permanent_event_state_version':state.permanent_event_state_version} if getattr(state,'permanent_event_state_version',None) is not None else {}),
        **({'battle_cry_resolution_version':state.battle_cry_resolution_version} if getattr(state,'battle_cry_resolution_version',None) is not None else {}),
        **({'ninjutsu_band_retention_version':state.ninjutsu_band_retention_version} if getattr(state,'ninjutsu_band_retention_version',None) is not None else {}),
        **({'attacking_entry_publication_version':state.attacking_entry_publication_version}
           if getattr(state,'attacking_entry_publication_version',None) is not None else {}),
        **({"ward_discard_order_version": ward_discard_order_version(state.ward_discard_order_version)}
           if getattr(state, "ward_discard_order_version", None) is not None else {}),
    }


def _validate_token_state_versions(format_value: Mapping[str, Any], state: Any) -> None:
    from .token_copy_value_model import predefined_token_name_version, token_copy_value_version

    for field, validate, label in (
        ("token_copy_value_version", token_copy_value_version, "Token copy value"),
        ("predefined_token_name_version", predefined_token_name_version, "Predefined token name"),
    ):
        declared = format_value.get(field, 0)
        actual = validate(getattr(state, field, None)) or 0
        if type(declared) is not int or declared not in {0, 1} or declared != actual:
            raise ValueError(f"{label} provenance does not match the initial checkpoint")


def _validate_mana_payment_versions(format_value: Mapping[str, Any], state: Any) -> None:
    if read_land_mana_tap_event_version(format_value) != getattr(state, LAND_MANA_TAP_VERSION_FIELD, None):
        raise ValueError("Land mana tap provenance does not match the initial checkpoint")
    if read_mana_source_counter_payment_version(format_value)!=getattr(state,'mana_source_counter_payment_version',None):
        raise ValueError('Mana source-counter payment provenance does not match the initial checkpoint')
    if read_mana_cost_order_version(format_value)!=mana_cost_order_version(getattr(state,'mana_cost_order_version',None)):
        raise ValueError('Mana cost-order provenance does not match the initial checkpoint')


def _validate_effect_versions(format_value: Mapping[str, Any], state: Any) -> None:
    if read_rad_trigger_version(format_value) != state.rad_trigger_version:
        raise ValueError("Inherent rad ability provenance does not match the initial checkpoint")
    validate_discard_effect_provenance(format_value, state)
    evolve_lki = evolve_entrant_lki_version(format_value.get(EVOLVE_ENTRANT_LKI_VERSION_FIELD))
    if (evolve_lki != getattr(state, EVOLVE_ENTRANT_LKI_VERSION_FIELD, None)
            or evolve_lki is None and EVOLVE_ENTRANT_LKI_VERSION_FIELD in format_value):
        raise ValueError("Evolve entrant LKI provenance does not match the initial checkpoint")
    if read_shield_counter_damage_version(format_value) != getattr(state, SHIELD_COUNTER_DAMAGE_VERSION_FIELD, None):
        raise ValueError("Shield counter damage provenance does not match the initial checkpoint")
    if read_damage_prevention_result_version(format_value) != getattr(state, DAMAGE_PREVENTION_RESULT_VERSION_FIELD, None):
        raise ValueError("Damage prevention result provenance does not match the initial checkpoint")
    if read_priority_concession_stabilization_version(format_value) != getattr(
        state, PRIORITY_CONCESSION_STABILIZATION_VERSION_FIELD, None
    ):
        raise ValueError("Priority concession stabilization provenance does not match the initial checkpoint")
    if read_battlefield_entry_player_version(format_value) != getattr(state, BATTLEFIELD_ENTRY_PLAYER_VERSION_FIELD, None):
        raise ValueError("Battlefield entry player provenance does not match the initial checkpoint")
    if read_continuous_dependency_order_version(format_value) != getattr(state, "continuous_dependency_order_version", None):
        raise ValueError("Continuous dependency order provenance does not match the initial checkpoint")
    if read_fetch_entry_placement_version(format_value) != getattr(state, "fetch_entry_placement_version", None):
        raise ValueError("Fetch entry placement provenance does not match the initial checkpoint")
    if land_entry_affordability_version(format_value.get(LAND_ENTRY_PAYMENT_VALIDATION_FIELD)) != getattr(
        state, LAND_ENTRY_PAYMENT_VALIDATION_FIELD, None,
    ):
        raise ValueError("Land entry payment validation provenance does not match the initial checkpoint")
    stack_authority = format_value.get("stack_resolution_authority_version", 0)
    if (
        type(stack_authority) is not int or stack_authority not in {0, 1, COPIED_SPELL_ENTRY_AUTHORITY_VERSION}
        or stack_authority != (stack_resolution_authority_version(getattr(state, "stack_resolution_authority_version", None)) or 0)
    ):
        raise ValueError("Stack resolution authority provenance does not match the initial checkpoint")


def _validate_cast_window_versions(format_value: Mapping[str, Any], state: Any) -> None:
    declared_attack = format_value.get('declare_attack_cast_version')
    if (('declare_attack_cast_version' in format_value
            and (type(declared_attack) is not int or declared_attack != 1))
            or declared_attack != declare_attack_cast_version(getattr(state, 'declare_attack_cast_version', None))):
        raise ValueError('Declared attack cast-window provenance does not match the checkpoint')
    upkeep_version = format_value.get("upkeep_completion_version")
    if (("upkeep_completion_version" in format_value and (type(upkeep_version) is not int or upkeep_version != 1))
            or upkeep_version != upkeep_completion_version(getattr(state, "upkeep_completed", None))):
        raise ValueError("Upkeep completion provenance does not match the initial checkpoint")


def validate_state_versions(
    manifest: Mapping[str, Any],
    state: Any,
) -> None:
    """Require manifest and initial-checkpoint semantic versions to agree."""

    validate_commander_damage_identity_provenance(
        manifest, state.commander_damage_identity_version
    )
    validate_control_history_provenance(
        manifest, state.control_history_version
    )
    format_value = manifest.get("format")
    if not isinstance(format_value, Mapping):
        raise ValueError("Record format provenance is malformed")
    validate_spell_name_provenance(format_value, state)
    _validate_cast_window_versions(format_value, state)
    _validate_mana_payment_versions(format_value, state)
    _validate_effect_versions(format_value, state)

    ward_order = format_value.get("ward_discard_order_version")
    checkpoint_ward_order = getattr(state, "ward_discard_order_version", None)
    if (ward_discard_order_version(ward_order) != checkpoint_ward_order
            or ward_order is None and "ward_discard_order_version" in format_value):
        raise ValueError("Ward discard order provenance does not match the initial checkpoint")
    departed_turns = format_value.get("departed_turn_boundary_version")
    if (departed_turns != getattr(state, "departed_turn_boundary_version", None)
            or "departed_turn_boundary_version" in format_value
            and (type(departed_turns) is not int or departed_turns != 1)):
        raise ValueError("Departed-turn boundary provenance does not match the initial checkpoint")
    declared = format_value.get("departure_history_version", 0)
    current = getattr(getattr(state, "turn_history", None), "departure_history_version", None) or 0
    if type(declared) is not int or declared not in {0, 1} or declared != current:
        raise ValueError("Departure-history provenance does not match the initial checkpoint")
    arrival=format_value.get('arrival_history_version',0)
    actual_arrival=getattr(getattr(state,'turn_history',None),'arrival_history_version',None) or 0
    if type(arrival) is not int or arrival not in {0,1} or arrival!=actual_arrival:raise ValueError('Arrival-history provenance does not match the initial checkpoint')
    for key in ('spell_origin_history_version','graveyard_type_history_version','spell_mana_history_version','spell_color_history_version','creature_spell_counter_history_version',
                'crime_history_version','committed_occurrence_history_version','damage_life_history_version',
                'permanent_death_history_version','death_name_history_version','first_life_gain_history_version','public_movement_history_version',
                'venture_history_version', 'plus_one_counter_placement_history_version',
                'land_play_history_version', 'permanent_counter_receipt_history_version', 'draw_identity_history_version'):
        declared=format_value.get(key,0);actual=getattr(getattr(state,'turn_history',None),key,None) or 0
        if type(declared) is not int or declared not in {0,1} or declared!=actual:raise ValueError('Qualified history provenance does not match the initial checkpoint')
    tap_version = format_value.get("tap_state_event_version", 0)
    if type(tap_version) is not int or tap_version not in {0, 1} or tap_version != (state.tap_state_event_version or 0):
        raise ValueError("Tap-state event provenance does not match the initial checkpoint")
    from .cast_source_placement_model import cast_source_placement_version
    from .explore_action_model import explore_action_version
    from .phasing_model import phasing_lifecycle_version

    phasing = format_value.get("phasing_lifecycle_version", 0)
    if (type(phasing) is not int
            or phasing != (phasing_lifecycle_version(getattr(state, "phasing_lifecycle_version", None)) or 0)):
        raise ValueError("Phasing lifecycle provenance does not match the initial checkpoint")
    _validate_token_state_versions(format_value, state)
    explore = format_value.get('explore_action_version', 0)
    if type(explore) is not int or explore not in {0, 1} or explore != (explore_action_version(getattr(state, 'explore_action_version', None)) or 0):
        raise ValueError('Explore action provenance does not match the initial checkpoint')
    placement=format_value.get('cast_source_placement_version',0)
    if type(placement) is not int or placement not in {0,1} or placement!=(cast_source_placement_version(getattr(state,'cast_source_placement_version',None)) or 0):
        raise ValueError('Casting source placement provenance does not match the initial checkpoint')
    if read_target_announcement_timing_version(format_value) != getattr(state, "target_announcement_timing_version", None):
        raise ValueError("Target announcement timing provenance does not match the initial checkpoint")
    if read_cast_event_type_snapshot_version(format_value) != getattr(state, "cast_event_type_snapshot_version", None):
        raise ValueError("Cast event type snapshot provenance does not match the initial checkpoint")
    if read_printed_mana_cost_version(format_value) != getattr(state, PRINTED_MANA_COST_VERSION_FIELD, None):
        raise ValueError("Printed mana cost authority provenance does not match the initial checkpoint")
    from .transform_timestamp_model import (
        modal_dfc_transform_version, transform_event_characteristics_version, transform_timestamp_version,
    )
    timestamp=format_value.get('transform_timestamp_version',0)
    if (type(timestamp) is not int or timestamp not in {0,1}
            or timestamp!=(transform_timestamp_version(getattr(state,'transform_timestamp_version',None)) or 0)):
        raise ValueError('Transform timestamp provenance does not match the initial checkpoint')
    modal_transform = format_value.get("modal_dfc_transform_version", 0)
    actual_modal_transform = modal_dfc_transform_version(getattr(state, "modal_dfc_transform_version", None)) or 0
    if type(modal_transform) is not int or modal_transform not in {0, 1} or modal_transform != actual_modal_transform:
        raise ValueError("Modal double-faced transform provenance does not match the initial checkpoint")
    split_face_field = "split_permanent_face_values_version"
    split_face_version = format_value.get(split_face_field)
    if (
        split_face_version != getattr(state, split_face_field, None)
        or split_face_field in format_value
        and (type(split_face_version) is not int or split_face_version not in {1, 2})
    ):
        raise ValueError("Split permanent face value provenance does not match the initial checkpoint")
    transform_characteristics = format_value.get("transform_event_characteristics_version", 0)
    if (type(transform_characteristics) is not int or transform_characteristics not in {0, 1}
            or transform_characteristics != (transform_event_characteristics_version(
                getattr(state, "transform_event_characteristics_version", None),
            ) or 0)):
        raise ValueError("Transform event characteristics provenance does not match the initial checkpoint")
    from .face_status_timestamp_model import face_status_timestamp_version
    face_status=format_value.get('face_status_timestamp_version',0)
    if (type(face_status) is not int or face_status not in {0,1}
            or face_status!=(face_status_timestamp_version(getattr(state,'face_status_timestamp_version',None)) or 0)):
        raise ValueError('Face-status timestamp provenance does not match the initial checkpoint')
    from .face_down_characteristics_model import face_down_characteristics_version, face_down_departure_reveal_version
    face_down=format_value.get('face_down_characteristics_version',0)
    if (type(face_down) is not int or face_down not in {0,1}
            or face_down!=(face_down_characteristics_version(getattr(state,'face_down_characteristics_version',None)) or 0)):
        raise ValueError('Face-down characteristic provenance does not match the initial checkpoint')
    reveal_field = "face_down_departure_reveal_version"
    if face_down_departure_reveal_version(format_value.get(reveal_field)) != getattr(state, reveal_field, None):
        raise ValueError("Face-down departure reveal provenance does not match the initial checkpoint")
    from .library_selection_knowledge_model import library_selection_knowledge_version
    knowledge=format_value.get('library_selection_knowledge_version',0)
    if (type(knowledge) is not int or knowledge not in {0,1}
            or knowledge!=(library_selection_knowledge_version(getattr(state,'library_selection_knowledge_version',None)) or 0)):
        raise ValueError('Library selection knowledge provenance does not match the initial checkpoint')
    from .characteristic_query_boundary_model import characteristic_query_boundary_version
    from .block_characteristic_model import block_characteristics_version
    from .unblocked_attacker_model import unblocked_attacker_event_version
    from .permanent_event_state_model import permanent_event_state_version
    from .battle_cry_resolution_model import battle_cry_resolution_version

    if unearth_leave_replacement_version(format_value.get("unearth_leave_replacement_version")) != getattr(
        state, "unearth_leave_replacement_version", None,
    ):
        raise ValueError("Unearth replacement provenance does not match the initial checkpoint")
    battle_cry=format_value.get('battle_cry_resolution_version',0)
    if (type(battle_cry) is not int or battle_cry not in {0,1}
            or battle_cry!=(battle_cry_resolution_version(getattr(state,'battle_cry_resolution_version',None)) or 0)):
        raise ValueError('Battle Cry resolution provenance does not match the initial checkpoint')
    _validate_combat_relationship_format(format_value, state)
    permanent_state=format_value.get('permanent_event_state_version',0)
    if (type(permanent_state) is not int
            or permanent_state!=(permanent_event_state_version(getattr(state,'permanent_event_state_version',None)) or 0)):
        raise ValueError('Permanent event-state version disagrees with checkpoint')
    unblocked=format_value.get('unblocked_attacker_event_version',0)
    if (type(unblocked) is not int or unblocked not in {0, 1, 2}
            or unblocked!=(unblocked_attacker_event_version(getattr(state,'unblocked_attacker_event_version',None)) or 0)):
        raise ValueError('Unblocked attacker event provenance does not match the initial checkpoint')
    blocks=format_value.get('block_characteristics_version',0)
    if (type(blocks)is not int or blocks not in {0,1}
            or blocks!=(block_characteristics_version(getattr(state,'block_characteristics_version',None)) or 0)):
        raise ValueError('Block characteristic facts provenance does not match the initial checkpoint')
    from .ability_fragment_primitives import SAGA_INTRINSIC_ABILITY_VERSION, saga_intrinsic_ability_version

    saga_version = format_value.get("saga_intrinsic_ability_version", 0)
    if (
        type(saga_version) is not int or saga_version not in {0, SAGA_INTRINSIC_ABILITY_VERSION}
        or saga_version != (saga_intrinsic_ability_version(getattr(state, "saga_intrinsic_ability_version", None)) or 0)
    ):
        raise ValueError("Saga intrinsic ability provenance does not match the initial checkpoint")
    boundary=format_value.get('characteristic_query_boundary_version',0)
    if (type(boundary) is not int or boundary not in {0,1,2}
            or boundary!=(characteristic_query_boundary_version(getattr(state,'characteristic_query_boundary_version',None)) or 0)):
        raise ValueError('Characteristic query boundary provenance does not match the initial checkpoint')
    from .state_based_legend_model import legend_rule_choice_version
    legend = format_value.get('legend_rule_choice_version', 0)
    if (type(legend) is not int or legend not in {0, 1}
            or legend != (legend_rule_choice_version(getattr(state, 'legend_rule_choice_version', None)) or 0)):
        raise ValueError('Legend choice provenance does not match the initial checkpoint')


__all__ = ["format_state_versions", "validate_state_versions"]
