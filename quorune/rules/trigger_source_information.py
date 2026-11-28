from __future__ import annotations

"""Read-only typed result-source information prepared by trigger orchestration."""

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from ..ability_source_information import retains_original_source_tap_status
from ..attachment_references import (
    ATTACHMENT_INFORMATION_VERSION,
    SourceAttachmentSnapshot,
    attachment_information_capabilities,
    capture_last_known_attachment_snapshot,
    capture_source_attachment_snapshot,
    required_attachment_relation,
)
from ..continuous_conditions import (
    FIXED_PUBLIC_STATE_INTERVENING_CONDITION_FIELD,
    FIXED_PUBLIC_STATE_INTERVENING_COVERAGE,
    FixedPublicStateConditionKind,
    FixedPublicStateConditionSpec,
)
from ..continuous_effect_model import ContinuousObjectIdentity
from ..object_predicate import ObjectQuerySpec, PermanentStatePredicateSpec
from ..replacement.immutable import FrozenMap
from .optional_counter_capability_shapes import opponent_damage_to_controller_condition

from ..creature_power_damage import capture_creature_power_damage_source_lki
from ..creature_power_damage_model import CREATURE_POWER_DAMAGE_LKI_CONTEXT


if TYPE_CHECKING:
    from ..model import CardInstance
    from ..semantics import SemanticProgram


def _trigger_attachment_payload(program: SemanticProgram):
    from .attached_greatest_power_condition import attached_greatest_power_event_condition

    condition = attached_greatest_power_event_condition(program.event_condition)
    return (*program.effects, condition.to_dict()) if condition is not None else program.effects


def _trigger_attachment_snapshot(
    cards: Mapping[str, CardInstance],
    source: CardInstance,
    program: SemanticProgram,
) -> SourceAttachmentSnapshot | None:
    relation = required_attachment_relation(_trigger_attachment_payload(program))
    if relation is None:
        return None
    if cards.get(source.object_id) is source:
        return capture_source_attachment_snapshot(cards, source, relation)
    attached = cards.get(source.attached_to or "")
    return capture_last_known_attachment_snapshot(
        cards,
        source,
        relation,
        source_logical_object_id=source.logical_object_id,
        attached_to_ref=attached.ref if attached is not None else None,
    )


def _versioned_attachment_information(program: SemanticProgram) -> dict[str, int]:
    if attachment_information_capabilities(_trigger_attachment_payload(program)):
        return {ATTACHMENT_INFORMATION_VERSION: 1}
    return {}


def trigger_attachment_context(
    cards: Mapping[str, CardInstance],
    source: CardInstance,
    program: SemanticProgram,
) -> dict[str, Any]:
    """Capture current or last-known attachment and designation information for a trigger."""
    from ..attachment_source_return import attachment_source_return_context
    from ..designation_prevention import (
        DESIGNATION_INFORMATION_FIELD,
        capture_program_designation_information,
    )

    designation = capture_program_designation_information(source, program)
    extra = {DESIGNATION_INFORMATION_FIELD: designation} if designation is not None else {}
    extra.update(attachment_source_return_context(source, program))
    snapshot = _trigger_attachment_snapshot(cards, source, program)
    if snapshot is None:
        return {"source_logical_object_id": source.logical_object_id, **extra}

    from .attachment_controller_information import capture_attachment_controller_context

    return {
        "source_logical_object_id": snapshot.source.logical_object_id,
        "source_attachment_snapshot": snapshot.to_dict(),
        **_versioned_attachment_information(program),
        **capture_attachment_controller_context(cards, program, snapshot),
        **extra,
    }


def trigger_result_source_information(host, *, source, program, event, stack_context, occurrence_context,
        source_characteristics, active_zone):
    """Collect typed result-source information after the occurrence has been sealed."""
    from .entry_x_graveyard_target import entry_x_graveyard_trigger_context
    from .entry_x_support import entry_x_support_trigger_context

    from .death_counter_shuffle import death_counter_shuffle_context
    from .kicked_artifact_entry import kicked_artifact_entry_trigger_context
    from .enchanted_departure_events import enchanted_departure_observer_context
    from .opposing_death_delayed_return import opposing_death_observer_context
    from .linked_death_token_copy import linked_death_token_leave_context

    from .linked_graveyard_mana import linked_graveyard_mana_source_context

    from .committed_nonland_mill import committed_mill_target_context

    from .cast_name_graveyard_returns import capture_cast_name_graveyard_return

    result = entry_x_graveyard_trigger_context(program, source, stack_context)
    result.update(linked_graveyard_mana_source_context(source, program, event))
    result.update(committed_mill_target_context(program, event, occurrence_context))
    result.update(capture_cast_name_graveyard_return(host, program, event, stack_context))
    result.update(kicked_artifact_entry_trigger_context(program, source, stack_context))
    result.update(death_counter_shuffle_context(source, program, event, occurrence_context))
    result.update(entry_x_support_trigger_context(program, source, stack_context))
    result.update(enchanted_departure_observer_context(source, program, event, occurrence_context))
    result.update(opposing_death_observer_context(source, program))
    result.update(linked_death_token_leave_context(source, program, event, occurrence_context))
    from ..phasing_model import phase_out_effect
    from ..extra_combat_model import uses_other_creature_extra_combat
    from .source_counter_distribution import uses_source_counter_distribution

    if (tuple(program.effects) == (phase_out_effect(),)
            or any(uses_source_counter_distribution(effect) or uses_other_creature_extra_combat(effect)
                   for effect in program.effects)):
        result["source_logical_object_id"] = source.logical_object_id
    from .gravestorm import GRAVESTORM_FIELD, gravestorm_program_dependencies, capture_gravestorm_cast_source
    if any(GRAVESTORM_FIELD in effect for effect in program.effects):
        if event != 'spell.cast' or active_zone != 'stack':
            raise ValueError('Gravestorm requires its source spell cast')
        gravestorm_program_dependencies(program)
        from ..event_spell_copy_model import EVENT_SPELL_COPY_CONTEXT
        result[EVENT_SPELL_COPY_CONTEXT] = capture_gravestorm_cast_source(host,source,stack_context).to_dict()
    from .demonstrate import DEMONSTRATE_FIELD, demonstrate_program_dependencies, capture_demonstrate_cast_source
    if any(DEMONSTRATE_FIELD in effect for effect in program.effects):
        if event != 'spell.cast' or active_zone != 'stack':
            raise ValueError('Demonstrate requires its source spell cast')
        demonstrate_program_dependencies(program)
        from ..event_spell_copy_model import EVENT_SPELL_COPY_CONTEXT
        result[EVENT_SPELL_COPY_CONTEXT] = capture_demonstrate_cast_source(host,source,stack_context).to_dict()
    from .ripple import RIPPLE_FIELD,RIPPLE_CAST_CONTEXT,ripple_program_dependencies,capture_ripple_cast_snapshot
    if any(RIPPLE_FIELD in effect for effect in program.effects):
        if event!='spell.cast' or active_zone!='stack':raise ValueError('Ripple source capture requires its actual spell cast')
        ripple_program_dependencies(program)
        result[RIPPLE_CAST_CONTEXT]=capture_ripple_cast_snapshot(host,source,stack_context,
            characteristics=source_characteristics).to_dict()
    from .revealed_library_cast import (
        MANA_VALUE_CAST_CONTEXT, MANA_VALUE_CAST_FIELD, capture_hand_cast_reveal_snapshot,
        mana_value_cast_program_dependencies,
    )
    if any(MANA_VALUE_CAST_FIELD in effect for effect in program.effects):
        mana_value_cast_program_dependencies(program)
        result[MANA_VALUE_CAST_CONTEXT] = capture_hand_cast_reveal_snapshot(occurrence_context, source.controller).to_dict()
    source_lki = capture_creature_power_damage_source_lki(
        host,
        source,
        program.effects,
        characteristics=source_characteristics,
    )
    if source_lki is not None:
        result[CREATURE_POWER_DAMAGE_LKI_CONTEXT] = source_lki
    from ..scalar_effect_amounts import scalar_source_context
    result.update(scalar_source_context(host, source, program.effects, characteristics=source_characteristics))
    from ..target_source import capture_targeting_source, TARGETING_SOURCE_CONTEXT
    from ..ability_source_information import complete_ability_source_information
    from ..fixed_token_production import uses_source_bound_populate
    from .endure_model import ENDURE_CAPABILITY
    source_bound_populate = any(uses_source_bound_populate(effect) for effect in program.effects)
    if source_bound_populate:
        result["source_logical_object_id"] = source.logical_object_id
    if (program.target_schema or source_bound_populate or complete_ability_source_information(host.state)
            or ENDURE_CAPABILITY in program.capability_dependencies):
        result[TARGETING_SOURCE_CONTEXT] = capture_targeting_source(
            host, source, characteristics=source_characteristics, zone=active_zone,
        ).to_dict()
    from .stack_damage_sources import fixed_damage_source_context
    result.update(fixed_damage_source_context(host, source, (program.effects, program.target_schema), characteristics=source_characteristics))
    from .combat_damage_entry_history import combat_damage_entry_trigger_context
    from .event_damage_sources import event_damage_source_context
    result.update(combat_damage_entry_trigger_context(program, event, occurrence_context))
    result.update(event_damage_source_context(host, program, event, {**stack_context, **result}))
    from ..token_copy_references import copy_source_context
    result.update(copy_source_context(host, source, program.effects))
    from .source_charge_life_totals import source_charge_life_trigger_context

    result.update(source_charge_life_trigger_context(source, program))
    from .discarded_card_cast import discarded_card_cast_source_context

    result.update(discarded_card_cast_source_context(host, source, program))
    result.update(original_source_tap_context(host.state, source, program))
    from .tap_state_capability_shapes import is_source_untap_counter_sequence

    if event in {"permanent.tap", "permanent.untap"} and is_source_untap_counter_sequence(
        program.effects, program.target_schema,
    ):
        result["source_logical_object_id"] = source.logical_object_id
    from .any_number_sacrifice import ANY_NUMBER_SACRIFICE_FIELD
    if any(ANY_NUMBER_SACRIFICE_FIELD in effect and effect.get("exclude_ref") == "$source.zone_object"
           for effect in program.effects):
        result["source_logical_object_id"] = source.logical_object_id
    return result


def damage_history_condition_value(host, field, *, source, context):
    """Resolve the closed damage-history qualifiers for normalized triggers."""
    from ..errors import GameRuleError
    from ..source_damage_death import SOURCE_DAMAGE_DEATH_FIELD, source_damage_death_matches
    from .combat_damage_entry_history import COMBAT_DAMAGE_ENTRY_FIELD, combat_damage_dealer_entered_this_turn

    if field == SOURCE_DAMAGE_DEATH_FIELD:
        return source_damage_death_matches(host, source, context)
    if field != COMBAT_DAMAGE_ENTRY_FIELD:
        return None
    try:
        return combat_damage_dealer_entered_this_turn(host.state, context)
    except (TypeError, ValueError) as exc:
        raise GameRuleError(str(exc)) from exc


UNTAPPED_DAMAGE_DRAW_VARIANT = "opponent_damage_untapped_source_draw"
UNTAPPED_DAMAGE_DRAW_TEMPLATE = "fixed-counter-opponent-damage-untapped-source-draw-v1"
UNTAPPED_DAMAGE_DRAW_COVERAGE = "original_untapped_damage_draw_v1"
_LEGACY_DAMAGE_SOURCE_STATUS = "untapped_damage_source_status"
_ORIGINAL_SOURCE_STATUS = "original_source_tap_status"


def _source_status_field(state):
    if retains_original_source_tap_status(state):
        return _ORIGINAL_SOURCE_STATUS
    return _LEGACY_DAMAGE_SOURCE_STATUS


def untapped_damage_source_condition():
    return FixedPublicStateConditionSpec(
        FixedPublicStateConditionKind.SOURCE_MATCHES_QUERY, schema_version=2,
        predicate=ObjectQuerySpec(zones=("battlefield",), state_predicate=PermanentStatePredicateSpec(tapped=False)),
    )


def _untapped_condition_atom():
    return {"field": FIXED_PUBLIC_STATE_INTERVENING_CONDITION_FIELD, "op": "truthy", "value": True,
            "condition": untapped_damage_source_condition().to_dict()}


_DRAW_STEP_UNTAPPED_CONDITION = FrozenMap({"all": [
    {"all": [{"field": "step", "op": "eq", "value": "draw"}]},
    _untapped_condition_atom(),
]})


def _event_condition():
    return {"all": [opponent_damage_to_controller_condition(), _untapped_condition_atom()]}


def uses_untapped_damage_source(program):
    template = UNTAPPED_DAMAGE_DRAW_TEMPLATE.replace("fixed-counter-", "fixed-typed-effect-", 1)
    return (UNTAPPED_DAMAGE_DRAW_COVERAGE in program.coverage
            or template in str(program.provenance.get("template_id") or "").split("+"))


def untapped_damage_draw_runtime_dependencies(program):
    marked = uses_untapped_damage_source(program)
    if not marked and program.event != "damage.dealt":
        return ()
    condition = isinstance(program.event_condition, Mapping) and FrozenMap(program.event_condition) == FrozenMap(_event_condition())
    if not marked and not condition:
        return ()
    expected = FrozenMap({"op": "offer_optional_effect", "player": "$controller", "effects": [
        {"op": "draw", "player": "$controller", "count": 1, "private": True},
    ]})
    required_coverage = {CURRENT_ABILITY_FRAGMENT_COVERAGE, FIXED_PUBLIC_STATE_INTERVENING_COVERAGE,
                         UNTAPPED_DAMAGE_DRAW_COVERAGE, "intervening_condition"}
    if (
        (program.event, program.active_zone) != ("damage.dealt", "battlefield") or not condition
        or program.target_schema is not None or program.cost_schema is not None or program.handlers
        or not required_coverage <= set(program.coverage) or "one_or_more_event_batch" in program.coverage
        or len(program.effects) != 1 or FrozenMap(program.effects[0]) != expected
    ):
        raise ValueError("An untapped damage draw requires its original source, opposing dealer and optional private draw")
    child, = program.effects[0]["effects"]
    if type(child["count"]) is not int or child["private"] is not True:
        raise ValueError("An untapped damage draw requires one private card")
    return ("trigger.event.normalized_damage", "trigger.condition.fixed_public_state",
            "effect.choice.optional_fixed", "zone.draw.library_to_hand", "trigger.placement.apnap")


TAPPED_DRAW_SOURCE_DAMAGE_CAPABILITY = "trigger.condition.tapped_draw_source_damage"
TAPPED_DRAW_SOURCE_DAMAGE_TEMPLATE = "fixed-counter-tapped-draw-source-damage-v1"


def tapped_draw_source_condition():
    return FixedPublicStateConditionSpec(
        FixedPublicStateConditionKind.SOURCE_MATCHES_QUERY, schema_version=2,
        predicate=ObjectQuerySpec(zones=("battlefield",), state_predicate=PermanentStatePredicateSpec(tapped=True)),
    )


def tapped_draw_damage_condition():
    return {"all": [
        {"all": [
            {"field": "player", "op": "eq", "value": "$source.controller"},
            {"field": "step", "op": "eq", "value": "draw"},
        ]},
        {"field": FIXED_PUBLIC_STATE_INTERVENING_CONDITION_FIELD, "op": "truthy", "value": True,
         "condition": tapped_draw_source_condition().to_dict()},
    ]}


_TAPPED_DRAW_DAMAGE_CONDITION = FrozenMap(tapped_draw_damage_condition())
_TAPPED_DRAW_DAMAGE_EFFECT = FrozenMap({"op": "damage", "source": "$source", "target": "$controller", "amount": 1})


def _tapped_draw_damage_shape(program):
    return (
        (program.event, program.active_zone) == ("step.begin", "battlefield")
        and FrozenMap(program.event_condition or {}) == _TAPPED_DRAW_DAMAGE_CONDITION
        and len(program.effects) == 1
        and FrozenMap(program.effects[0]) == _TAPPED_DRAW_DAMAGE_EFFECT
        and type(program.effects[0].get("amount")) is int
    )


def tapped_draw_damage_runtime_dependencies(program):
    template = TAPPED_DRAW_SOURCE_DAMAGE_TEMPLATE.replace("fixed-counter-", "fixed-typed-effect-", 1)
    marked = (TAPPED_DRAW_SOURCE_DAMAGE_CAPABILITY in program.capability_dependencies
              or template in str(program.provenance.get("template_id") or "").split("+"))
    closed = _tapped_draw_damage_shape(program)
    if not marked and not closed:
        return ()
    if (not closed or program.target_schema is not None or program.cost_schema is not None or program.handlers
            or not {"intervening_condition", FIXED_PUBLIC_STATE_INTERVENING_COVERAGE} <= set(program.coverage)):
        raise ValueError("Tapped draw damage requires its original source, own draw step, and fixed controller damage")
    return (TAPPED_DRAW_SOURCE_DAMAGE_CAPABILITY, "trigger.condition.fixed_public_state")


def original_source_tap_expectation(state, program) -> bool | None:
    if uses_untapped_damage_source(program):
        return False
    if _tapped_draw_damage_shape(program):
        return True
    if (
        program.event == "step.begin"
        and program.active_zone == "battlefield"
        and FIXED_PUBLIC_STATE_INTERVENING_COVERAGE in program.coverage
        and retains_original_source_tap_status(state)
        and isinstance(program.event_condition, Mapping)
        and FrozenMap(program.event_condition) == _DRAW_STEP_UNTAPPED_CONDITION
    ):
        return False
    return None


def original_source_tap_context(state, source, program):
    if original_source_tap_expectation(state, program) is None:
        return {}
    identity = ContinuousObjectIdentity(source.object_id, source.logical_object_id)
    return {"source_logical_object_id": identity.logical_object_id,
            _source_status_field(state): {"schema_version": 1, "source": identity.to_dict(), "departure_tapped": None}}


def _read_source_status(context, field):
    value = context.get(field)
    if (
        not isinstance(value, Mapping) or set(value) != {"schema_version", "source", "departure_tapped"}
        or type(value["schema_version"]) is not int or value["schema_version"] != 1
        or value["departure_tapped"] is not None and type(value["departure_tapped"]) is not bool
    ):
        raise ValueError("An original source condition requires its versioned tap observation")
    identity = ContinuousObjectIdentity.from_dict(value["source"])
    if identity.logical_object_id != context.get("source_logical_object_id"):
        raise ValueError("A source tap condition lost its original identity")
    return value, identity


def pin_untapped_damage_source_departures(host, cards, *, error_type):
    departing = {card.logical_object_id: card for card in cards if card.zone == "battlefield"}
    if not departing:
        return

    field = _source_status_field(host.state)

    def capture(context):
        value, identity = _read_source_status(context, field)
        source = departing.get(identity.logical_object_id)
        if source is None or source.object_id != identity.object_id or value["departure_tapped"] is not None:
            return None
        return {**value, "departure_tapped": source.tapped}

    from ..trigger_processing import pin_trigger_context_entry

    try:
        pin_trigger_context_entry(host, field, capture)
    except (TypeError, ValueError) as exc:
        raise error_type(str(exc)) from exc


def recheck_original_source_tap(host, context, *, event, controller, source_object_id, expected_tapped):
    value, identity = _read_source_status(context, _source_status_field(host.state))
    if (identity.object_id != source_object_id or context.get("event") != event
            or context.get("intervening_condition_controller") != controller
            or event == "step.begin" and context.get("step") != "draw"
            or expected_tapped and context.get("player") != controller):
        raise ValueError("A source tap condition changed its original occurrence or controller")
    source = host.state.cards.get(identity.object_id)
    if source is not None and source.zone == "battlefield" and source.logical_object_id == identity.logical_object_id:
        return source.tapped is expected_tapped
    if value["departure_tapped"] is None:
        raise ValueError("A departed source requires its last known tap status")
    return value["departure_tapped"] is expected_tapped
