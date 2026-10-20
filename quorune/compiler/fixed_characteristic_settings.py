from __future__ import annotations

"""Closed static setting grammar over the existing public and attachment queries."""

import re

from ..object_predicate import ObjectQuerySpec
from ..creature_subtypes import CREATURE_SUBTYPES
from .continuous_templates import (
    _attached_modifier, _attached_definition, _grant_attached_abilities,
    _attached_ability_capabilities,_attached_abilities,
)
from .creature_subtypes import canonical_creature_subtype_surface
from .public_state_queries import fixed_characteristic_battlefield_query_subject, fixed_public_state_parts
from ..rules.source_references import SourceReferenceSpec


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
    # CR 205.1b: becoming an artifact creature retains other card types.
    if {'artifact', 'creature'} <= {word.casefold() for word in words}:
        modifier['type_operations'] = [
            {**op, 'op': 'add_types'} if op['field'] == 'card_types' and op['op'] == 'set_types' else op
            for op in modifier['type_operations']
        ]
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
    removed=re.fullmatch(r'loses (.+)',part,re.I)
    if removed:
        from ..keyword_abilities import FIXED_CHARACTERISTIC_KEYWORDS
        abilities=_attached_abilities(removed[1])
        if abilities is None or any(value not in FIXED_CHARACTERISTIC_KEYWORDS for value in abilities):
            return False
        if set(abilities).intersection(modifier['remove_abilities']):
            return False
        modifier['remove_abilities'].extend(abilities)
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


def fixed_characteristic_setting_handler(text: str, *, source_name: str | None = None):
    source_condition = None
    if source_name is not None:
        parsed = fixed_public_state_parts(text, source_name=source_name)
        if parsed is not None:
            source_condition, text = parsed
            if re.fullmatch(rf"(?:This (?:creature|artifact|enchantment|permanent)|{SourceReferenceSpec(source_name).regex_pattern}) isn['’]t a creature\.?", text.strip(), re.I):
                from ..semantic_runtime.fixed_characteristic_settings import (
                    ConditionalCreatureTypeRemovalHandler, CREATURE_TYPE_REMOVAL_HANDLER, CREATURE_TYPE_REMOVAL_CAPABILITY,
                )
                descriptor = {'handler_id': CREATURE_TYPE_REMOVAL_HANDLER, 'schema_version': 1,
                    'event': 'characteristics.evaluate', 'source_condition': source_condition.to_dict()}
                try:
                    ConditionalCreatureTypeRemovalHandler().validate(descriptor)
                except (TypeError, ValueError):
                    return None
                return 'static-conditional-creature-type-removal-v1', descriptor, (CREATURE_TYPE_REMOVAL_CAPABILITY,)
            from ..semantic_runtime.fixed_characteristic_settings import validate_setting_condition
            try:
                validate_setting_condition(source_condition.to_dict())
            except (TypeError, ValueError):
                return None
            text = re.sub(r'^it\b', 'This permanent', text, flags=re.I)
    if re.search(r'"|\b(?:chosen|named|equal|each equal|every|all colors|land types|creature types|until|as long|if|during|goaded)\b', text, re.I):
        return None
    text = re.sub(r'\s+\([^()]*\)\.?$', '', text.strip()).removesuffix(".")
    match = re.fullmatch(r"(?P<subject>.+?) (?P<body>is .+|are .+|has base .+|have base .+|loses .+|lose .+|gets .+|get .+)", text, re.I)
    if match is None:
        return None
    subject, body = match["subject"], match["body"]
    attached = re.fullmatch(r"(?:Enchanted|Equipped) (artifact|battle|creature|enchantment|land|permanent|planeswalker)", subject, re.I)
    source = source_name is not None and re.fullmatch(
        rf'(?:This (?:creature|artifact|permanent)|{SourceReferenceSpec(source_name).regex_pattern})', subject, re.I
    )
    if source:
        target = {'kind': 'source', 'controller': None,
                  'predicate': ObjectQuerySpec(zones=('battlefield',)).to_dict(), 'exclude_source': False}
    elif attached:
        kind = attached[1].casefold()
        predicate = ObjectQuerySpec(zones=("battlefield",), types_all=() if kind == "permanent" else (kind,))
        target = {"kind": "attached", "controller": None, "predicate": predicate.to_dict(), "exclude_source": False}
    else:
        parsed = fixed_characteristic_battlefield_query_subject(subject)
        if parsed is None:
            return None
        relation, predicate, exclude = parsed
        target = {"kind": "fixed_query", "controller": relation, "predicate": predicate.to_dict(), "exclude_source": exclude}
    # A fixed P/T creature definition is a type change plus layer-7b setting.
    creature_definition = re.fullmatch(r'is (?:a |an )?(-?\d+)/(-?\d+) (.+?)(?: with (.+))?', body, re.I)
    if creature_definition is not None:
        body = (f'is {creature_definition[3]} with base power and toughness '
                f'{creature_definition[1]}/{creature_definition[2]}')
        if creature_definition[4]:
            body += ' and has ' + creature_definition[4]
    modifier = _attached_modifier()
    parts = re.split(r",\s*(?:and )?| and (?=(?:are|is|have|has|lose|loses|get|gets)\b)", body, flags=re.I)
    removes=[part for part in parts if re.fullmatch(r'(?:and )?(?:lose|loses) all(?: other)? abilities',part.strip(),re.I)]
    if len(removes)>1 or (removes and re.search(r'\b(?:have|has) (?!base )',body,re.I) and not re.search(r'\b(?:lose|loses) all other abilities\b',body,re.I)):
        return None
    if not all(_setting_part(modifier, part) for part in parts):
        return None
    if attached is None and not any((modifier['type_operations'], modifier['color_operations'],
                                     modifier['base_power'] is not None, modifier['remove_all_abilities'],modifier['remove_abilities'])):
        return None
    version = 2 if source or source_condition else 1
    descriptor={"handler_id":'continuous.characteristics.fixed-public-setting.v2' if version == 2 else SETTING_HANDLER,
        "schema_version":version,
        "event":"characteristics.evaluate","target":target,"modifier":modifier}
    if descriptor['schema_version'] == 2:
        descriptor['source_condition'] = source_condition.to_dict() if source_condition is not None else None
    from ..semantic_runtime.fixed_characteristic_settings import FixedCharacteristicSettingsHandler, ConditionalCharacteristicSettingsHandler
    try:
        (ConditionalCharacteristicSettingsHandler() if version == 2 else FixedCharacteristicSettingsHandler()).validate(descriptor)
    except (TypeError,ValueError):
        return None
    return ("static-fixed-characteristic-setting-v2" if descriptor['schema_version'] == 2 else "static-fixed-characteristic-setting-v1",
        descriptor, tuple(sorted({SETTING_CAPABILITY, *_attached_ability_capabilities((*modifier["add_abilities"],*modifier["remove_abilities"])),
        *(('continuous.characteristics.fixed_public_state',) if version == 2 else ())})))
