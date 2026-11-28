"""Closed entry designation and current faction branch capability shapes."""

from collections.abc import Mapping

from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from ..continuous_conditions import (
    FIXED_PUBLIC_STATE_CHARACTERISTICS_HANDLER_ID,
    FixedPublicStateConditionKind, FixedPublicStateConditionSpec, FixedPublicStateFact,
)
from ..entry_designations import (
    ENTRY_DESIGNATION_CAPABILITY, ENTRY_DESIGNATION_HANDLER_ID,
    ENTRY_JESKAI_TEMUR_CAPABILITY, EntryDesignationKind,
)
from ..keyword_abilities import FIXED_CHARACTERISTIC_KEYWORD_CAPABILITIES
from ..object_predicate import ObjectQuerySpec
from ..replacement.immutable import FrozenMap


KIND = EntryDesignationKind.JESKAI_TEMUR_FACTION
ENTRY_CAPABILITIES = (ENTRY_DESIGNATION_CAPABILITY, ENTRY_JESKAI_TEMUR_CAPABILITY)
JESKAI_CONDITION_FIELD = f"source_annotation.{KIND.annotation}"
JESKAI_EVENT = "damage.dealt"
JESKAI_COVERAGE = ("one_or_more_event_batch", CURRENT_ABILITY_FRAGMENT_COVERAGE)
JESKAI_CAPABILITIES = (
    *ENTRY_CAPABILITIES, "trigger.placement.apnap", "trigger.event.normalized_damage",
    "trigger.effect.fixed_event", "zone.draw.library_to_hand",
)
TEMUR_FACT = FixedPublicStateFact.SOURCE_JESKAI_TEMUR_TEMUR_CHOSEN
TEMUR_KEYWORDS = ("Trample", "Haste")
TEMUR_CAPABILITIES = (
    *ENTRY_CAPABILITIES, "continuous.characteristics.fixed_public_state",
    "continuous.ability.fixed_query_keyword_grant", "continuous.power_toughness.fixed_anthem",
    *(capability for keyword in TEMUR_KEYWORDS
      for capability in FIXED_CHARACTERISTIC_KEYWORD_CAPABILITIES[keyword]),
)


def entry_descriptor():
    return {"handler_id": ENTRY_DESIGNATION_HANDLER_ID, "schema_version": 1,
            "event": "zone.change", "designation": KIND.value}


def jeskai_condition():
    return {"all": [
        {"field": JESKAI_CONDITION_FIELD, "op": "eq", "value": "Jeskai"},
        {"field": "source_zone", "op": "eq", "value": "battlefield"},
        {"field": "source_controller", "op": "eq", "value": "$source.controller"},
        {"field": "source_types", "op": "contains_any", "value": ["creature"]},
        {"field": "combat", "op": "eq", "value": True},
        {"field": "target_kind", "op": "eq", "value": "player"},
        {"field": "amount", "op": "gte", "value": 1},
    ]}


def jeskai_effect():
    return {"op": "draw", "player": "$controller", "count": 1, "private": True}


def temur_condition():
    return FixedPublicStateConditionSpec(
        FixedPublicStateConditionKind.PUBLIC_FACT_AT_LEAST,
        amount=1, fact=TEMUR_FACT, schema_version=4,
    )


def temur_descriptor():
    return {
        "handler_id": FIXED_PUBLIC_STATE_CHARACTERISTICS_HANDLER_ID, "schema_version": 1,
        "event": "characteristics.evaluate", "source_condition": temur_condition().to_dict(),
        "target": {"kind": "fixed_query", "target_controller": "source_controller",
                   "predicate": ObjectQuerySpec(zones=("battlefield",), types_all=("creature",)).to_dict(),
                   "exclude_source": False, "types_all": []},
        "modifier": {"add_abilities": list(TEMUR_KEYWORDS), "power": 1, "toughness": 0},
    }


def _mentions_branch(value, field, expected):
    if isinstance(value, Mapping):
        return value.get(field) == expected or any(
            _mentions_branch(child, field, expected) for child in value.values()
        )
    return isinstance(value, (tuple, list)) and any(_mentions_branch(child, field, expected) for child in value)


def _matches_handler(program, descriptor, active_zone):
    return (
        len(program.handlers) == 1 and type(program.handlers[0].get("schema_version")) is int
        and FrozenMap(program.handlers[0]) == FrozenMap(descriptor)
        and (program.event, program.active_zone) == (descriptor["event"], active_zone)
        and not program.effects and program.event_condition is None
    )


def jeskai_temur_runtime_dependencies(program):
    declared = ENTRY_JESKAI_TEMUR_CAPABILITY in program.capability_dependencies
    entry = any(handler.get("designation") == KIND.value for handler in program.handlers)
    jeskai = _mentions_branch(program.event_condition, "field", JESKAI_CONDITION_FIELD)
    temur = _mentions_branch(program.handlers, "fact", TEMUR_FACT.value)
    if not (declared or entry or jeskai or temur):
        return ()
    if program.target_schema is None and program.cost_schema is None and program.trigger_limit is None:
        if entry and _matches_handler(program, entry_descriptor(), "all"):
            return ENTRY_CAPABILITIES
        if (jeskai and (program.event, program.active_zone) == (JESKAI_EVENT, "battlefield")
                and set(JESKAI_COVERAGE).issubset(program.coverage)
                and not program.handlers and len(program.effects) == 1
                and type(program.effects[0].get("count")) is int and program.effects[0].get("private") is True
                and FrozenMap(program.effects[0]) == FrozenMap(jeskai_effect())
                and FrozenMap(program.event_condition) == FrozenMap(jeskai_condition())):
            return JESKAI_CAPABILITIES
        if temur and _matches_handler(program, temur_descriptor(), "battlefield"):
            return TEMUR_CAPABILITIES
    raise ValueError("The faction designation requires its exact entry or selected branch")
