from __future__ import annotations
from collections.abc import Mapping

"""Closed Hexproof keyword values and source-quality comparisons."""

TURN_COLOR_SPELL_PROTECTION_CAPABILITY = "resolution.turn_counter_and_color_hexproof"
TURN_COLOR_SPELL_PROTECTION_MECHANIC = "fixed-turn-counter-and-color-hexproof"
TURN_COLOR_SPELL_PROTECTION_OPERATION = "grant_uncounterable_hexproof_from_colors_until_end"
TURN_PLAYER_COLOR_HEXPROOF = "player_hexproof_from_colors_until_end"


QUALIFIED_HEXPROOF_CAPABILITY='target.protection.hexproof_qualified'
HEXPROOF_COLORS={'white':'W','blue':'U','black':'B','red':'R','green':'G'}
HEXPROOF_TYPES={'artifacts':'artifact','battles':'battle','creatures':'creature','enchantments':'enchantment',
    'instants':'instant','lands':'land','planeswalkers':'planeswalker','sorceries':'sorcery'}
QUALIFIED_HEXPROOF_KEYWORDS=frozenset('hexproof from '+value for value in (*HEXPROOF_COLORS,*HEXPROOF_TYPES,'monocolored','multicolored'))


def color_hexproof_keywords(colors):
    return tuple(f"hexproof from {name}" for name, color in HEXPROOF_COLORS.items() if color in colors)


def uses_turn_color_spell_protection(effect):
    return (effect.get("op") == TURN_COLOR_SPELL_PROTECTION_OPERATION and "schema_version" in effect)


def turn_color_spell_protection_colors(effect, *, player):
    if (not isinstance(effect, Mapping) or set(effect) != {"op", "schema_version", "player", "colors"}
            or effect.get("op") != TURN_COLOR_SPELL_PROTECTION_OPERATION
            or type(effect.get("schema_version")) is not int or effect["schema_version"] != 2
            or effect.get("player") != player):
        raise ValueError("Turn-long spell and color protection requires its exact controller and version")
    colors = effect["colors"]
    if (not isinstance(colors, (list, tuple)) or len(colors) != 2
            or any(type(color) is not str or color not in HEXPROOF_COLORS.values() for color in colors)
            or tuple(colors) != tuple(color for color in HEXPROOF_COLORS.values() if color in colors)):
        raise ValueError("Turn-long spell and color protection requires two distinct canonical colors")
    return tuple(colors)


def keyword_type_terms(keywords):
    values={str(value).casefold() for value in keywords}
    return values|({'hexproof'} if values.intersection(QUALIFIED_HEXPROOF_KEYWORDS) else set())


def keyword_is_removed(actual,requested):
    actual=str(actual).casefold();requested=str(requested).casefold()
    return actual==requested or requested=='hexproof' and actual in QUALIFIED_HEXPROOF_KEYWORDS


def qualified_hexproof_prohibits(keywords,source):
    variants=set(keywords).intersection(QUALIFIED_HEXPROOF_KEYWORDS)
    if not variants:return False
    if source is None:return True
    for keyword in variants:
        quality=keyword.removeprefix('hexproof from ')
        if quality in HEXPROOF_TYPES:
            if source.card_types is None or HEXPROOF_TYPES[quality] in source.card_types:return True
        elif source.colors is None:return True
        elif quality in HEXPROOF_COLORS:
            if HEXPROOF_COLORS[quality] in source.colors:return True
        elif quality=='monocolored' and len(source.colors)==1:return True
        elif quality=='multicolored' and len(source.colors)>=2:return True
    return False


def qualified_hexproof_runtime_dependencies(program):
    def contains(value):
        if isinstance(value,str):return value.casefold() in QUALIFIED_HEXPROOF_KEYWORDS
        if isinstance(value,Mapping):return any(contains(v) for v in value.values())
        return isinstance(value,(tuple,list)) and any(contains(v) for v in value)
    return (QUALIFIED_HEXPROOF_CAPABILITY,) if contains((program.coverage,program.handlers,program.effects,program.target_schema)) else ()
