from __future__ import annotations

"""Closed static setting grammar over the existing public and attachment queries."""

import re

from ..object_predicate import ObjectQuerySpec
from ..creature_subtypes import CREATURE_SUBTYPES
from .continuous_templates import (
    _attached_modifier, _attached_definition, _grant_attached_abilities,
    _attached_ability_capabilities,
)
from .creature_subtypes import canonical_creature_subtype_surface
from .public_state_queries import fixed_characteristic_battlefield_query_subject


SETTING_HANDLER = "continuous.characteristics.fixed-public-setting.v1"
SETTING_CAPABILITY = "continuous.characteristics.fixed_public_setting"
_COLORS = {"white": "W", "blue": "U", "black": "B", "red": "R", "green": "G"}


def _definition(modifier, text, *, addition):
    words = text.split()
    normalized = []
    for word in words:
        subtype = canonical_creature_subtype_surface(word)
        normalized.append(subtype.title() if subtype is not None else word)
    if not _attached_definition(modifier, " ".join(normalized), addition=addition):
        return False
    operations=[]
    for operation in modifier['type_operations']:
        if operation['op']=='set_types' and operation['field']=='subtypes':
            operations.append({'op':'remove_types','field':'subtypes','values':sorted(value.title() for value in CREATURE_SUBTYPES)})
            operations.append({**operation,'op':'add_types'})
        else:
            operations.append(operation)
    modifier['type_operations']=operations
    return not any(op["field"] == "subtypes" and any(v.casefold() in {"plains", "island", "swamp", "mountain", "forest"} for v in op["values"]) for op in modifier["type_operations"])


def _setting_part(modifier, part):
    part = part.strip().removeprefix("and ")
    part = re.sub(r"^(?:have|are|lose|get)\b", lambda m: {"have": "has", "are": "is", "lose": "loses", "get": "gets"}[m[0].casefold()], part, flags=re.I)
    base = re.fullmatch(r"has base power and toughness (-?\d+)/(-?\d+)", part, re.I)
    if base:
        if modifier['base_power'] is not None:
            return False
        modifier.update(base_power=int(base[1]), base_toughness=int(base[2]))
        return True
    delta = re.fullmatch(r"gets ([+-]\d+)/([+-]\d+)", part, re.I)
    if delta:
        modifier['power'] += int(delta[1])
        modifier['toughness'] += int(delta[2])
        return True
    if re.fullmatch(r"loses all(?: other)? abilities", part, re.I):
        if modifier['remove_all_abilities']:
            return False
        modifier["remove_all_abilities"] = True
        return True
    abilities = re.fullmatch(r"has (.+)", part, re.I)
    if abilities:
        return _grant_attached_abilities(modifier, abilities[1])
    definition = re.fullmatch(r"is (.+?)(?: in addition to (?:its|their) other (?:(colors and )?types|colors))?", part, re.I)
    if definition:
        addition = bool(re.search(r" in addition to ", part, re.I))
        value = definition[1]
        if value.casefold() == "colorless":
            modifier["color_operations"].append({"op": "remove_all_colors"})
            return True
        if value.casefold() in _COLORS:
            modifier["color_operations"].append({"op": "add_colors" if addition else "set_colors", "values": [_COLORS[value.casefold()]]})
            return True
        base_definition = re.fullmatch(r"(.+?) with base power and toughness (-?\d+)/(-?\d+)", value, re.I)
        if base_definition:
            if modifier['base_power'] is not None:
                return False
            modifier.update(base_power=int(base_definition[2]), base_toughness=int(base_definition[3]))
            value = base_definition[1]
        return _definition(modifier, value, addition=addition)
    return False


def fixed_characteristic_setting_handler(text: str):
    if re.search(r'"|\b(?:chosen|named|equal|each equal|every|all colors|land types|creature types|until|as long|if|during|goaded)\b', text, re.I):
        return None
    text = text.strip().removesuffix(".")
    match = re.fullmatch(r"(?P<subject>.+?) (?P<body>is .+|are .+|has base .+|have base .+|loses all .+|lose all .+|gets .+|get .+)", text, re.I)
    if match is None:
        return None
    subject, body = match["subject"], match["body"]
    attached = re.fullmatch(r"(?:Enchanted|Equipped) (artifact|battle|creature|enchantment|land|permanent|planeswalker)", subject, re.I)
    if attached:
        kind = attached[1].casefold()
        predicate = ObjectQuerySpec(zones=("battlefield",), types_all=() if kind == "permanent" else (kind,))
        target = {"kind": "attached", "controller": None, "predicate": predicate.to_dict(), "exclude_source": False}
    else:
        parsed = fixed_characteristic_battlefield_query_subject(subject)
        if parsed is None:
            return None
        relation, predicate, exclude = parsed
        target = {"kind": "fixed_query", "controller": relation, "predicate": predicate.to_dict(), "exclude_source": exclude}
    modifier = _attached_modifier()
    parts = re.split(r",\s*(?:and )?| and (?=(?:are|is|have|has|lose|loses|get|gets)\b)", body, flags=re.I)
    removes=[part for part in parts if re.fullmatch(r'(?:and )?(?:lose|loses) all(?: other)? abilities',part.strip(),re.I)]
    if len(removes)>1 or (removes and re.search(r'\b(?:have|has) (?!base )',body,re.I) and not re.search(r'\b(?:lose|loses) all other abilities\b',body,re.I)):
        return None
    if not all(_setting_part(modifier, part) for part in parts):
        return None
    if not any((modifier["type_operations"], modifier["color_operations"], modifier["base_power"] is not None, modifier["remove_all_abilities"])):
        return None
    descriptor={"handler_id":SETTING_HANDLER,"schema_version":1,"event":"characteristics.evaluate","target":target,"modifier":modifier}
    from ..semantic_runtime.fixed_characteristic_settings import FixedCharacteristicSettingsHandler
    try:
        FixedCharacteristicSettingsHandler().validate(descriptor)
    except (TypeError,ValueError):
        return None
    return ("static-fixed-characteristic-setting-v1", descriptor, tuple(sorted({SETTING_CAPABILITY, *_attached_ability_capabilities(modifier["add_abilities"])})))
