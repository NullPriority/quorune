"""Compile bounded controlled-entry event subjects with their linked result."""

from . import regex_working_set as re
from ..creature_subtypes import canonical_creature_subtype, canonical_creature_subtype_surface
from ..rules.qualified_entry_effects import (
    ENTRY_FLYING_HASTE_VARIANT, ENTRY_SUBTYPE_DAMAGE_VARIANT, controlled_flying_entry_condition,
    controlled_subtype_entry_condition, entry_subtype_damage_capabilities, entry_subtype_damage_effect,
    entry_subtype_damage_target_schema,
    flying_entry_haste_effect, flying_entry_haste_node_capabilities,
)

_ENTRY_FLYING = re.compile(
    r"Whenever a creature you control with flying enters, (?P<body>it gains haste until end of turn\.)", re.I,
)
_ENTRY_SUBTYPE = re.compile(r"Whenever an? (?P<subtype>.+?) you control enters, (?P<body>.+)", re.I)
_ENTRY_DAMAGE = re.compile(r"It deals X damage to any target, where X is the number of (?P<subtypes>.+?) you control\.", re.I)


def qualified_entry_result_binding(text, *, spec):
    if match := _ENTRY_FLYING.fullmatch(text):
        return spec(
            "creature.enter", ENTRY_FLYING_HASTE_VARIANT, match["body"], "controlled-flying-entry-haste-v1",
            "trigger-event-normalized-zone-change", condition=controlled_flying_entry_condition(),
            capabilities=flying_entry_haste_node_capabilities(effects=(flying_entry_haste_effect(),), target_schema=None),
        )
    if match := _ENTRY_SUBTYPE.fullmatch(text):
        subtype = canonical_creature_subtype(match["subtype"])
        damage = _ENTRY_DAMAGE.fullmatch(match["body"])
        if subtype is not None and damage is not None and canonical_creature_subtype_surface(damage["subtypes"]) == subtype:
            return spec(
                "permanent.enter", ENTRY_SUBTYPE_DAMAGE_VARIANT, match["body"], "controlled-subtype-entry-count-damage-v1",
                "trigger-event-normalized-zone-change", condition=controlled_subtype_entry_condition(subtype),
                capabilities=entry_subtype_damage_capabilities(
                    entry_subtype_damage_effect(subtype), entry_subtype_damage_target_schema(),
                ),
            )
    return None


def qualified_entry_result(binding, body):
    if body != binding.body:
        return None
    if binding.variant == ENTRY_FLYING_HASTE_VARIANT and re.fullmatch(r"it gains haste until end of turn\.", body, re.I):
        return (
            "controlled-flying-entry-haste-v1", (flying_entry_haste_effect(),), None,
            ("cr-611-continuous-effects", "haste"),
        ), False
    if binding.variant != ENTRY_SUBTYPE_DAMAGE_VARIANT:
        return None
    match = _ENTRY_DAMAGE.fullmatch(body)
    if match is None:
        return None
    subtype = canonical_creature_subtype_surface(match["subtypes"])
    if subtype is None or binding.event_condition != controlled_subtype_entry_condition(subtype):
        return None
    effect = entry_subtype_damage_effect(subtype)
    targets = entry_subtype_damage_target_schema()
    if not entry_subtype_damage_capabilities(effect, targets):
        return None
    from .public_query_effect_amounts import PUBLIC_QUERY_EFFECT_AMOUNT_MECHANIC
    return ("controlled-subtype-entry-count-damage-v1", (effect,), targets,
            ("cr-120-damage", "cr-115-targets", PUBLIC_QUERY_EFFECT_AMOUNT_MECHANIC)), False
