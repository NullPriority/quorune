from __future__ import annotations

"""A closed announced subtype-sacrifice price for ordinary temporary stats."""

from collections.abc import Mapping
import re

from ..creature_subtypes import canonical_creature_subtype, canonical_creature_subtype_surface
from ..object_predicate import ObjectQuerySpec
from ..replacement.immutable import thaw_value
from ..util import mana_cost_to_vector


ANNOUNCED_SUBTYPE_SACRIFICE_KIND = "sacrifice_announced_subtype"
ANNOUNCED_SUBTYPE_SACRIFICE_CAPABILITY = "activation.selected_zone_change.announced_subtype_sacrifice"
ANNOUNCED_SUBTYPE_STATS_TEXT = "target creature gets +x/-x until end of turn."

_PRICE = re.compile(r"(?P<mana>(?:\{(?:[1-9]\d*|[WUBRGC])\})+), Sacrifice X (?P<subtype>[A-Za-z][A-Za-z '-]*)")


def printed_announced_subtype_price(text):
    match = _PRICE.fullmatch(" ".join(text.split()))
    if match is None:
        return None
    subtype = canonical_creature_subtype_surface(match["subtype"])
    if subtype is None:
        return None
    return mana_cost_to_vector(match["mana"])[0], subtype


def announced_subtype_sacrifice_predicate(subtype):
    return ObjectQuerySpec(zones=("battlefield",), controller="$actor", subtypes_all=(subtype,), known_to_actor=True)


def validate_announced_subtype_choice(choice):
    if choice.kind != ANNOUNCED_SUBTYPE_SACRIFICE_KIND:
        return
    if (choice.count != 0 or choice.zone != "battlefield" or choice.another
            or any(getattr(choice, field) is not None for field in (
                "card_type", "counter_amount", "tap_schema_version", "return_schema_version",
                "sacrifice_schema_version", "predicate_union",
            ))):
        raise ValueError("Announced subtype sacrifice requires its one typed zero-minimum choice")
    if not isinstance(choice.predicate, Mapping):
        raise ValueError("Announced subtype sacrifice requires its controlled subtype query")
    query = ObjectQuerySpec.from_dict(choice.predicate)
    if (len(query.subtypes_all) != 1 or canonical_creature_subtype(query.subtypes_all[0]) is None
            or query != announced_subtype_sacrifice_predicate(query.subtypes_all[0])):
        raise ValueError("Announced subtype sacrifice requires exactly one controlled permanent subtype")


def announced_subtype_sacrifice_choice(ability):
    return next((choice for choice in ability.choices if choice.kind == ANNOUNCED_SUBTYPE_SACRIFICE_KIND), None)


def validate_announced_subtype_ability(ability):
    choice = announced_subtype_sacrifice_choice(ability)
    if choice is None:
        return
    printed = printed_announced_subtype_price(ability.cost_text)
    if (printed is None or ability.zones != ("battlefield",) or tuple(ability.choices) != (choice,)
            or " ".join(ability.effect_text.split()).casefold() != ANNOUNCED_SUBTYPE_STATS_TEXT
            or any(getattr(ability, field) for field in (
                "tap_source", "untap_source", "discard_source", "sacrifice_source", "exile_source",
                "mana_ability", "uncompiled_costs", "complex_symbols", "life_payment", "energy_payment",
                "random_discard_payment", "mana_cost_options", "waterbend", "loyalty_x", "mill_library_cost",
            )) or any(getattr(ability, field) is not None for field in (
                "announced_x_symbols", "loyalty_delta", "source_counter_removal_cost",
                "source_counter_placement_cost", "variable_colored_reveal_price", "ability_local_mana_reduction",
            ))):
        raise ValueError("Announced subtype sacrifice uses its closed ordinary mana and X activation")
    mana, subtype = printed
    if (dict(ability.mana) != mana
            or ObjectQuerySpec.from_dict(choice.predicate) != announced_subtype_sacrifice_predicate(subtype)):
        raise ValueError("Announced subtype sacrifice differs from the printed price")


def is_announced_subtype_cost_schema(cost):
    return (isinstance(cost, Mapping) and any(
        isinstance(choice, Mapping) and choice.get("k") == ANNOUNCED_SUBTYPE_SACRIFICE_KIND
        for choice in cost.get("choices", ())
    ))


def announced_subtype_activation_runtime_dependencies(program):
    from ..abilities import ActivatedAbility
    from ..semantic_runtime.activated_abilities import is_structural_activated_ability_catalog_program

    if is_structural_activated_ability_catalog_program(program):
        return ()
    price = program.cost_schema
    catalog = tuple(handler for handler in program.handlers if handler.get("handler_id") == "activation.catalog.pinned.v1")
    serialized = catalog[0].get("ability", {}) if len(catalog) == 1 else {}
    if not isinstance(serialized, Mapping):
        serialized = {}
    has_choice = any(isinstance(row, Mapping) and row.get("kind") == ANNOUNCED_SUBTYPE_SACRIFICE_KIND
                     for row in serialized.get("choices", ()))
    if not is_announced_subtype_cost_schema(price) and not has_choice:
        return ()
    if ((program.event, program.active_zone) != ("activate", "battlefield")
            or len(catalog) != 1 or len(program.handlers) != 1
            or not isinstance(serialized, Mapping) or not isinstance(price, Mapping)):
        raise ValueError("Announced subtype sacrifice lost its active source catalog or price")
    ability = ActivatedAbility.from_dict(thaw_value(serialized))
    choice = announced_subtype_sacrifice_choice(ability)
    target_schema = {"zones": ["battlefield"], "categories": ["permanent"], "count": 1, "types_any": ["creature"]}
    effects = [{"op": "modify_stats_until_end_of_turn", "card": "$target.0", "power": "$x", "toughness": "$neg_x"}]
    if (choice is None or thaw_value(price.get("choices")) != [choice.compact()]
            or price.get("text") != ability.cost_text or thaw_value(price.get("mana")) != dict(ability.mana)
            or thaw_value(program.target_schema) != target_schema or thaw_value(program.effects) != effects):
        raise ValueError("Announced subtype sacrifice requires the same X in its cost and opposite signed target stats")
    return (ANNOUNCED_SUBTYPE_SACRIFICE_CAPABILITY,)
