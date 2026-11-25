"""Lower exact collective optional fixed-damage spell and source-entry text."""

from . import regex_working_set as re
from .destruction_templates import mass_destruction_effect_template
from .fixed_numbers import FIXED_COUNT_PATTERN, fixed_number
from ..rules.collective_source_damage import (
    COLLECTIVE_SOURCE_DAMAGE_MECHANIC,
    collective_creature_destruction_capabilities,
    collective_source_damage_instruction,
)
from ..rules.source_references import SourceReferenceSpec
from ..source_sacrifice_model import SOURCE_SACRIFICE_MECHANIC, fixed_source_sacrifice_effect


_SPELL = re.compile(
    r"Any player may have (?P<source>.+?) deal (?P<amount>[1-9][0-9]*) damage to them\. "
    r"If no one does, (?P<body>.+)", re.I,
)
_TARGETED_RESULT = re.compile(
    rf"target player (?P<result>draws|mills) (?P<count>{FIXED_COUNT_PATTERN}) cards?\.", re.I,
)
_ENTRY = re.compile(
    r"Any opponent may have (?P<source>.+?) deal (?P<amount>[1-9][0-9]*) damage to them\. "
    r"If a player does, sacrifice this creature\.", re.I,
)


def collective_source_damage_spell_template(text, *, card_name, source_is_permanent):
    if source_is_permanent is not False:
        return None
    match = _SPELL.fullmatch(" ".join(text.split()))
    if match is None or not (match["source"].casefold() == "this spell" or SourceReferenceSpec(card_name).matches(match["source"])):
        return None
    targeted = _TARGETED_RESULT.fullmatch(match["body"])
    if targeted is None:
        destruction = mass_destruction_effect_template(match["body"])
        if destruction is None or destruction.target_schema is not None:
            return None
        result, = destruction.effects
        if not collective_creature_destruction_capabilities(result, destruction.mechanics):
            return None
        return (
            "collective-optional-source-damage-destruction-v1",
            (collective_source_damage_instruction(int(match["amount"]), "players", result),),
            None,
            (COLLECTIVE_SOURCE_DAMAGE_MECHANIC, "cr-120-damage", *destruction.mechanics),
        )
    count = fixed_number(targeted["count"])
    if type(count) is not int or count <= 0:
        return None
    result = {"op": targeted["result"].casefold()[:-1], "player": "$target.0", "count": count}
    mechanics = ("mill",)
    if result["op"] == "draw":
        result["private"] = True
        mechanics = ("cr-121-drawing-a-card",)
    schema = {"zones": ["player"], "categories": ["player"], "count": 1, "player_relation": "any"}
    return ("collective-optional-source-damage-v1",
        (collective_source_damage_instruction(int(match["amount"]), "players", result),), schema,
        (COLLECTIVE_SOURCE_DAMAGE_MECHANIC, "cr-115-targets", "cr-120-damage", *mechanics))


def collective_source_damage_entry_template(text, *, card_name):
    match = _ENTRY.fullmatch(" ".join(text.split()))
    if match is None or not (match["source"].casefold() in {"it", "this creature"}
                             or SourceReferenceSpec(card_name).matches(match["source"])):
        return None
    return ("collective-optional-source-damage-entry-v1",
        (collective_source_damage_instruction(int(match["amount"]), "opponents", fixed_source_sacrifice_effect()),), None,
        (COLLECTIVE_SOURCE_DAMAGE_MECHANIC, SOURCE_SACRIFICE_MECHANIC, "cr-120-damage"))
