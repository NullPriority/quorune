from __future__ import annotations

"""Additive Game Record v3 state-semantics provenance facade."""

from typing import Any, Mapping

from .stack_resolution_authority_model import stack_resolution_authority_version
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


def format_state_versions(state: Any) -> dict[str, int]:
    """Return canonical manifest markers for additive state semantics."""

    from .state_based_legend_model import legend_rule_choice_version
    legend = legend_rule_choice_version(getattr(state, 'legend_rule_choice_version', None))
    stack_authority = stack_resolution_authority_version(getattr(state, "stack_resolution_authority_version", None))
    departed_turns = getattr(state, "departed_turn_boundary_version", None)
    return {
        **({'legend_rule_choice_version': legend} if legend is not None else {}),
        **({"stack_resolution_authority_version": stack_authority} if stack_authority is not None else {}),
        **({"departed_turn_boundary_version": departed_turns} if departed_turns is not None else {}),
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
        **{key:1 for key in ('spell_origin_history_version','graveyard_type_history_version','spell_mana_history_version',
            'crime_history_version','committed_occurrence_history_version','damage_life_history_version')
            if getattr(getattr(state,'turn_history',None),key,None)==1},
        **({"tap_state_event_version": state.tap_state_event_version} if state.tap_state_event_version is not None else {}),
        **({'cast_source_placement_version':state.cast_source_placement_version} if getattr(state,'cast_source_placement_version',None) is not None else {}),
        **({"phasing_lifecycle_version": state.phasing_lifecycle_version}
           if getattr(state, "phasing_lifecycle_version", None) is not None else {}),
        **printed_mana_cost_format_fields(getattr(state, PRINTED_MANA_COST_VERSION_FIELD, None)),
        **({'explore_action_version':state.explore_action_version} if getattr(state,'explore_action_version',None) is not None else {}),
        **({'transform_timestamp_version':state.transform_timestamp_version} if getattr(state,'transform_timestamp_version',None) is not None else {}),
        **({"modal_dfc_transform_version": state.modal_dfc_transform_version}
           if getattr(state, "modal_dfc_transform_version", None) is not None else {}),
        **({'face_status_timestamp_version':state.face_status_timestamp_version} if getattr(state,'face_status_timestamp_version',None) is not None else {}),
        **({'face_down_characteristics_version':state.face_down_characteristics_version} if getattr(state,'face_down_characteristics_version',None) is not None else {}),
        **({'library_selection_knowledge_version':state.library_selection_knowledge_version} if getattr(state,'library_selection_knowledge_version',None) is not None else {}),
        **({'characteristic_query_boundary_version':state.characteristic_query_boundary_version} if getattr(state,'characteristic_query_boundary_version',None) is not None else {}),
        **({'token_copy_value_version':state.token_copy_value_version} if getattr(state,'token_copy_value_version',None) is not None else {}),
        **({"predefined_token_name_version": state.predefined_token_name_version}
           if getattr(state, "predefined_token_name_version", None) is not None else {}),
        **({'block_characteristics_version':state.block_characteristics_version} if getattr(state,'block_characteristics_version',None) is not None else {}),
        **({'unblocked_attacker_event_version':state.unblocked_attacker_event_version} if getattr(state,'unblocked_attacker_event_version',None) is not None else {}),
        **({'permanent_event_state_version':state.permanent_event_state_version} if getattr(state,'permanent_event_state_version',None) is not None else {}),
        **({'battle_cry_resolution_version':state.battle_cry_resolution_version} if getattr(state,'battle_cry_resolution_version',None) is not None else {}),
        **({'ninjutsu_band_retention_version':state.ninjutsu_band_retention_version} if getattr(state,'ninjutsu_band_retention_version',None) is not None else {}),
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
    stack_authority = format_value.get("stack_resolution_authority_version", 0)
    if (
        type(stack_authority) is not int or stack_authority not in {0, 1}
        or stack_authority != (stack_resolution_authority_version(getattr(state, "stack_resolution_authority_version", None)) or 0)
    ):
        raise ValueError("Stack resolution authority provenance does not match the initial checkpoint")
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
    for key in ('spell_origin_history_version','graveyard_type_history_version','spell_mana_history_version',
                'crime_history_version','committed_occurrence_history_version','damage_life_history_version'):
        declared=format_value.get(key,0);actual=getattr(getattr(state,'turn_history',None),key,None) or 0
        if type(declared) is not int or declared not in {0,1} or declared!=actual:raise ValueError('Qualified history provenance does not match the initial checkpoint')
    tap_version = format_value.get("tap_state_event_version", 0)
    if type(tap_version) is not int or tap_version not in {0, 1} or tap_version != (state.tap_state_event_version or 0):
        raise ValueError("Tap-state event provenance does not match the initial checkpoint")
    from .cast_source_placement_model import cast_source_placement_version
    from .explore_action_model import explore_action_version
    from .phasing_model import phasing_lifecycle_version

    phasing = format_value.get("phasing_lifecycle_version", 0)
    if (type(phasing) is not int or phasing not in {0, 1}
            or phasing != (phasing_lifecycle_version(getattr(state, "phasing_lifecycle_version", None)) or 0)):
        raise ValueError("Phasing lifecycle provenance does not match the initial checkpoint")
    _validate_token_state_versions(format_value, state)
    explore = format_value.get('explore_action_version', 0)
    if type(explore) is not int or explore not in {0, 1} or explore != (explore_action_version(getattr(state, 'explore_action_version', None)) or 0):
        raise ValueError('Explore action provenance does not match the initial checkpoint')
    placement=format_value.get('cast_source_placement_version',0)
    if type(placement) is not int or placement not in {0,1} or placement!=(cast_source_placement_version(getattr(state,'cast_source_placement_version',None)) or 0):
        raise ValueError('Casting source placement provenance does not match the initial checkpoint')
    if read_printed_mana_cost_version(format_value) != getattr(state, PRINTED_MANA_COST_VERSION_FIELD, None):
        raise ValueError("Printed mana cost authority provenance does not match the initial checkpoint")
    from .transform_timestamp_model import modal_dfc_transform_version, transform_timestamp_version
    timestamp=format_value.get('transform_timestamp_version',0)
    if (type(timestamp) is not int or timestamp not in {0,1}
            or timestamp!=(transform_timestamp_version(getattr(state,'transform_timestamp_version',None)) or 0)):
        raise ValueError('Transform timestamp provenance does not match the initial checkpoint')
    modal_transform = format_value.get("modal_dfc_transform_version", 0)
    actual_modal_transform = modal_dfc_transform_version(getattr(state, "modal_dfc_transform_version", None)) or 0
    if type(modal_transform) is not int or modal_transform not in {0, 1} or modal_transform != actual_modal_transform:
        raise ValueError("Modal double-faced transform provenance does not match the initial checkpoint")
    from .face_status_timestamp_model import face_status_timestamp_version
    face_status=format_value.get('face_status_timestamp_version',0)
    if (type(face_status) is not int or face_status not in {0,1}
            or face_status!=(face_status_timestamp_version(getattr(state,'face_status_timestamp_version',None)) or 0)):
        raise ValueError('Face-status timestamp provenance does not match the initial checkpoint')
    from .face_down_characteristics_model import face_down_characteristics_version
    face_down=format_value.get('face_down_characteristics_version',0)
    if (type(face_down) is not int or face_down not in {0,1}
            or face_down!=(face_down_characteristics_version(getattr(state,'face_down_characteristics_version',None)) or 0)):
        raise ValueError('Face-down characteristic provenance does not match the initial checkpoint')
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
    from .banding_model import ninjutsu_band_retention_version
    battle_cry=format_value.get('battle_cry_resolution_version',0)
    if (type(battle_cry) is not int or battle_cry not in {0,1}
            or battle_cry!=(battle_cry_resolution_version(getattr(state,'battle_cry_resolution_version',None)) or 0)):
        raise ValueError('Battle Cry resolution provenance does not match the initial checkpoint')
    ninjutsu_bands = format_value.get('ninjutsu_band_retention_version')
    if ('ninjutsu_band_retention_version' in format_value and (type(ninjutsu_bands) is not int or ninjutsu_bands != 1)
        or ninjutsu_bands != ninjutsu_band_retention_version(getattr(state, 'ninjutsu_band_retention_version', None))):
        raise ValueError('Ninjutsu band retention provenance does not match the initial checkpoint')
    permanent_state=format_value.get('permanent_event_state_version',0)
    if (type(permanent_state) is not int or permanent_state not in {0,1}
            or permanent_state!=(permanent_event_state_version(getattr(state,'permanent_event_state_version',None)) or 0)):
        raise ValueError('Permanent event-state version disagrees with checkpoint')
    unblocked=format_value.get('unblocked_attacker_event_version',0)
    if (type(unblocked)is not int or unblocked not in {0,1}
            or unblocked!=(unblocked_attacker_event_version(getattr(state,'unblocked_attacker_event_version',None)) or 0)):
        raise ValueError('Unblocked attacker event provenance does not match the initial checkpoint')
    blocks=format_value.get('block_characteristics_version',0)
    if (type(blocks)is not int or blocks not in {0,1}
            or blocks!=(block_characteristics_version(getattr(state,'block_characteristics_version',None)) or 0)):
        raise ValueError('Block characteristic facts provenance does not match the initial checkpoint')
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
