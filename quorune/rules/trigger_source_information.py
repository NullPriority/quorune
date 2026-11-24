from __future__ import annotations

"""Read-only typed result-source information prepared by trigger orchestration."""

from collections.abc import Mapping

from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from ..ability_source_information import retains_original_source_tap_status
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


def trigger_result_source_information(host, *, source, program, event, stack_context, occurrence_context,
        source_characteristics, active_zone):
    """Collect typed result-source information after the occurrence has been sealed."""
    from .entry_x_graveyard_target import entry_x_graveyard_trigger_context

    result = entry_x_graveyard_trigger_context(program, source, stack_context)
    from ..phasing_model import phase_out_effect
    if tuple(program.effects) == (phase_out_effect(),):
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
    from .endure_model import ENDURE_CAPABILITY
    if program.target_schema or complete_ability_source_information(host.state) or ENDURE_CAPABILITY in program.capability_dependencies:
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
    result.update(original_source_tap_context(host.state, source, program))
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


def uses_original_untapped_source(state, program):
    if uses_untapped_damage_source(program):
        return True
    return (
        program.event == "step.begin"
        and program.active_zone == "battlefield"
        and FIXED_PUBLIC_STATE_INTERVENING_COVERAGE in program.coverage
        and retains_original_source_tap_status(state)
        and isinstance(program.event_condition, Mapping)
        and FrozenMap(program.event_condition) == _DRAW_STEP_UNTAPPED_CONDITION
    )


def original_source_tap_context(state, source, program):
    if not uses_original_untapped_source(state, program):
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
        raise ValueError("An untapped source condition requires its versioned tap observation")
    identity = ContinuousObjectIdentity.from_dict(value["source"])
    if identity.logical_object_id != context.get("source_logical_object_id"):
        raise ValueError("An untapped source condition lost its original source identity")
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


def recheck_untapped_damage_source(host, context, *, event, controller, source_object_id):
    value, identity = _read_source_status(context, _source_status_field(host.state))
    if (identity.object_id != source_object_id or context.get("event") != event
            or context.get("intervening_condition_controller") != controller
            or event == "step.begin" and context.get("step") != "draw"):
        raise ValueError("An untapped source condition changed its original occurrence or controller")
    source = host.state.cards.get(identity.object_id)
    if source is not None and source.zone == "battlefield" and source.logical_object_id == identity.logical_object_id:
        return not source.tapped
    if value["departure_tapped"] is None:
        raise ValueError("A departed source requires its last known tap status")
    return not value["departure_tapped"]
