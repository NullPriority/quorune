"""Typed result shapes for controlled flying and subtype entry occurrences."""

from collections.abc import Mapping

from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from ..characteristic_fragments import CharacteristicQuantityScope, CharacteristicQuantitySpec
from ..creature_subtypes import canonical_creature_subtype
from ..event_damage_source_model import EVENT_DAMAGE_SOURCE_CAPABILITY, EventDamageSourceReference
from ..object_predicate import ObjectQuerySpec
from ..query_effect_amount_model import PUBLIC_QUERY_AMOUNT_CAPABILITY, PUBLIC_QUERY_AMOUNT_KIND, PublicQueryAmountSpec
from ..replacement.immutable import thaw_value

ENTRY_FLYING_HASTE_VARIANT = "controlled_flying_entry_haste"
ENTRY_SUBTYPE_DAMAGE_VARIANT = "controlled_subtype_entry_count_damage"
ENTRY_FLYING_HASTE_CAPABILITY = "trigger.effect.controlled_flying_entry_haste"
ENTRY_SUBTYPE_DAMAGE_CAPABILITY = "trigger.effect.controlled_subtype_entry_count_damage"
_ENTRY_CAPABILITIES = (
    "trigger.event.normalized_zone_change", "trigger.effect.fixed_event", "trigger.placement.apnap",
)
_CONTROLLER = {"field": "controller", "op": "eq", "value": "$source.controller"}


def controlled_flying_entry_condition():
    return {"all": [dict(_CONTROLLER), {"field": "keywords", "op": "contains_any", "value": ["flying"]}]}


def controlled_subtype_entry_condition(subtype):
    if subtype is None or canonical_creature_subtype(subtype) != subtype:
        raise ValueError("Controlled entry requires a canonical subtype")
    return {"all": [dict(_CONTROLLER), {"field": "subtypes", "op": "contains_any", "value": [subtype]}]}


def flying_entry_haste_effect():
    return {"op": "grant_keyword_until_end_of_turn", "card": "$event.permanent_object", "keyword": "Haste"}


def flying_entry_haste_node_capabilities(*, effects, target_schema, mechanic_ids=()):
    del mechanic_ids
    if target_schema is not None or tuple(thaw_value(effects)) != (flying_entry_haste_effect(),):
        return ()
    return (
        ENTRY_FLYING_HASTE_CAPABILITY, "resolution.event_object.fixed_result", "combat.block.flying",
        "combat.attack.haste", "activation.tap_untap_cost.haste",
        "continuous.resolution.fixed_characteristics_until_end_of_turn", *_ENTRY_CAPABILITIES,
    )


def flying_entry_haste_runtime_dependencies(program):
    from .effect_shape_walkers import nested_effects

    leaves = tuple(effect for effect in nested_effects((program.effects, program.handlers))
                   if effect.get("op") == "grant_keyword_until_end_of_turn"
                   and effect.get("card") == "$event.permanent_object")
    if not leaves and ENTRY_FLYING_HASTE_CAPABILITY not in program.capability_dependencies:
        return ()
    required = flying_entry_haste_node_capabilities(effects=program.effects, target_schema=program.target_schema)
    if (
        not required or leaves != tuple(program.effects) or program.handlers or program.cost_schema is not None
        or (program.event, program.active_zone) != ("creature.enter", "battlefield")
        or thaw_value(program.event_condition) != controlled_flying_entry_condition()
        or CURRENT_ABILITY_FRAGMENT_COVERAGE not in program.coverage
    ):
        raise ValueError("Entry haste requires its sole originally controlled flying creature occurrence")
    return required


def entry_subtype_damage_effect(subtype):
    quantity = CharacteristicQuantitySpec(
        scope=CharacteristicQuantityScope.CONTROLLER_ZONE,
        query=ObjectQuerySpec(zones=("battlefield",), subtypes_all=(subtype,)),
    )
    return {
        "op": "damage", "source": EventDamageSourceReference("permanent.enter").to_dict(),
        "target": "$target.0", "amount": PublicQueryAmountSpec(quantity).to_dict(),
    }


def entry_subtype_damage_subtype(effect):
    amount = effect.get("amount")
    if not isinstance(amount, Mapping) or amount.get("kind") != PUBLIC_QUERY_AMOUNT_KIND:
        return None
    spec = PublicQueryAmountSpec.from_dict(amount)
    quantity = spec.quantity
    query = getattr(quantity, "query", None)
    subtypes = getattr(query, "subtypes_all", ())
    if len(subtypes) != 1 or canonical_creature_subtype(subtypes[0]) != subtypes[0]:
        return None
    subtype, = subtypes
    return subtype if thaw_value(effect) == entry_subtype_damage_effect(subtype) else None


def entry_subtype_damage_target_schema():
    from .damage_capability_shapes import _FIXED_DAMAGE_TARGET_SCHEMAS

    return thaw_value(_FIXED_DAMAGE_TARGET_SCHEMAS["any_target"])


def entry_subtype_damage_capabilities(effect, target_schema):
    from .damage_capability_shapes import fixed_damage_node_capabilities

    if (entry_subtype_damage_subtype(effect) is None
            or thaw_value(target_schema) != entry_subtype_damage_target_schema()
            or type(target_schema["count"]) is not int):
        return ()
    base = fixed_damage_node_capabilities(
        effects=({"op": "damage", "source": "$source", "target": "$target.0", "amount": 1},),
        target_schema=target_schema, mechanic_ids=("cr-120-damage", "cr-115-targets"),
    )
    return (*base, EVENT_DAMAGE_SOURCE_CAPABILITY, PUBLIC_QUERY_AMOUNT_CAPABILITY,
            ENTRY_SUBTYPE_DAMAGE_CAPABILITY, *_ENTRY_CAPABILITIES) if base else ()


def validate_entry_subtype_damage_program(program, leaves, subtype):
    if (
        leaves != tuple(program.effects) or program.handlers or program.cost_schema is not None
        or (program.event, program.active_zone) != ("permanent.enter", "battlefield")
        or thaw_value(program.event_condition) != controlled_subtype_entry_condition(subtype)
        or CURRENT_ABILITY_FRAGMENT_COVERAGE not in program.coverage
    ):
        raise ValueError("Entry damage requires the same controlled entering subtype as the live count")
