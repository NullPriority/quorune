"""Lower one original player's optional damage choice and fixed alternate result."""

from . import regex_working_set as re
from .counter_templates import targeted_counter_effect_template
from .damage_templates import fixed_damage_effect_template
from .destruction_templates import targeted_destruction_effect_template
from .fixed_numbers import FIXED_COUNT_PATTERN, fixed_number
from ..random_discard import random_discard_effect_template
from ..rules.optional_source_damage import (
    OPTIONAL_SOURCE_DAMAGE_MECHANIC, milled_mana_damage_choice, milled_mana_damage_target_schema, source_damage_choice,
)
from ..rules.source_references import SourceReferenceSpec
from ..target_controller_reference import TARGET_CONTROLLER_INFORMATION_MECHANIC, target_controller_reference


_DRAW = re.compile(
    rf"Target opponent may have (?P<source>.+?) deal (?P<damage>[1-9][0-9]*) damage to them\. "
    rf"If that player doesn['’]t, you draw (?P<count>{FIXED_COUNT_PATTERN}) cards?\.", re.I,
)
_MILLED_MANA_ENTRY = re.compile(
    rf"Target opponent may have you draw (?P<draw>{FIXED_COUNT_PATTERN}) cards?\. "
    rf"If the player doesn['\u2019]t, you mill (?P<mill>{FIXED_COUNT_PATTERN}) cards?, then "
    r"(?P<source>.+?) deals damage to that player equal to the total mana value of those cards\.", re.I,
)

_DISCARD = re.compile(
    r"(?P<alternate>Target player discards? .+? at random) unless that player has "
    r"(?P<source>.+?) deal (?P<damage>[1-9][0-9]*) damage to them\.", re.I,
)


_DESTROY = re.compile(
    r"(?P<alternate>Destroy target nonbasic land) unless its controller has "
    r"(?P<source>.+?) deal (?P<damage>[1-9][0-9]*) damage to them\.", re.I,
)


_CREATURE_CONTROLLER_DAMAGE = re.compile(
    r"(?P<primary_source>.+?) deals (?P<primary>[1-9][0-9]*) damage to target creature "
    r"unless that creature['’]s controller has (?P<source>.+?) deal (?P<damage>[1-9][0-9]*) damage to them\.",
    re.I,
)
_SPELL_CONTROLLER_DAMAGE = re.compile(
    r"Counter target instant or sorcery spell unless its controller has "
    r"(?P<source>.+?) deal (?P<damage>[1-9][0-9]*) damage to them\.",
    re.I,
)


def _optional_target_controller_damage(body, card_name):
    from ..rules.stack_counter_followup import STACK_TARGET_CONTROLLER

    creature = _CREATURE_CONTROLLER_DAMAGE.fullmatch(body)
    match = creature or _SPELL_CONTROLLER_DAMAGE.fullmatch(body)
    if match is None:
        return None
    source = SourceReferenceSpec(card_name)
    subjects = (match["source"], creature["primary_source"]) if creature is not None else (match["source"],)
    if any(subject.casefold() != "this spell" and not source.matches(subject) for subject in subjects):
        return None
    if creature is not None:
        template = fixed_damage_effect_template(
            f"This spell deals {creature['primary']} damage to target creature.", card_name=card_name,
        )
        chooser = target_controller_reference()
        binding = (TARGET_CONTROLLER_INFORMATION_MECHANIC,)
    else:
        template = targeted_counter_effect_template("Counter target instant or sorcery spell.")
        chooser = STACK_TARGET_CONTROLLER
        binding = ()
    if template is None:
        return None
    _, (alternate,), schema, mechanics = template.compiled()
    damage = {"op": "damage", "source": "$source", "target": chooser, "amount": int(match["damage"])}
    return (
        "fixed-optional-target-controller-damage-v1",
        (source_damage_choice(damage, alternate, chooser=chooser),),
        schema,
        tuple(dict.fromkeys((OPTIONAL_SOURCE_DAMAGE_MECHANIC, *binding, "cr-120-damage", *mechanics))),
    )


def optional_milled_mana_damage_entry_template(text, *, card_name):
    from .mill_templates import MILL_MECHANIC_ID

    match = _MILLED_MANA_ENTRY.fullmatch(" ".join(text.split()))
    if match is None:
        return None
    source = match["source"]
    if source.casefold() != "this creature" and not SourceReferenceSpec(card_name).matches(source):
        return None
    draw, mill = fixed_number(match["draw"]), fixed_number(match["mill"])
    if not draw or not mill:
        return None
    return (
        "fixed-entry-opponent-draw-or-milled-mana-damage-v1",
        (milled_mana_damage_choice(draw, mill),),
        milled_mana_damage_target_schema(),
        (OPTIONAL_SOURCE_DAMAGE_MECHANIC, "cr-115-targets", "cr-120-damage", "cr-121-drawing-a-card", MILL_MECHANIC_ID),
    )


def optional_source_damage_template(text, *, card_name, source_is_permanent):
    if source_is_permanent is not False:
        return None
    body = " ".join(text.split())
    controlled = _optional_target_controller_damage(body, card_name)
    if controlled is not None:
        return controlled
    draw = _DRAW.fullmatch(body)
    destroy = _DESTROY.fullmatch(body)
    match = draw or destroy or _DISCARD.fullmatch(body)
    if match is None:
        return None
    source = match["source"]
    if source.casefold() != "this spell" and not SourceReferenceSpec(card_name).matches(source):
        return None
    if destroy:
        template = targeted_destruction_effect_template(f"{destroy['alternate']}.")
        if template is None:
            return None
        _, (alternate,), schema, mechanics = template.compiled()
        controller = target_controller_reference()
        damage = {"op": "damage", "source": "$source", "target": controller, "amount": int(match["damage"])}
        return (
            "fixed-optional-land-controller-damage-v1",
            (source_damage_choice(damage, alternate, chooser=controller),),
            schema,
            (OPTIONAL_SOURCE_DAMAGE_MECHANIC, TARGET_CONTROLLER_INFORMATION_MECHANIC, "cr-120-damage", *mechanics),
        )
    recipient = "target opponent" if draw else "target player"
    lowered = fixed_damage_effect_template(
        f"This spell deals {match['damage']} damage to {recipient}.", card_name=card_name,
    )
    if lowered is None:
        return None
    _, damage, schema, mechanics = lowered.compiled()
    if draw:
        count = fixed_number(draw["count"])
        if count is None or count <= 0:
            return None
        alternate = {"op": "draw", "player": "$controller", "count": count, "private": True}
        fallback_mechanics = ("cr-121-drawing-a-card",)
    else:
        result = random_discard_effect_template(match["alternate"])
        if result is None or result[2] != {**schema, "player_relation": "any"}:
            return None
        alternate, = result[1]
        schema = result[2]
        fallback_mechanics = result[3]
    effect = source_damage_choice(damage[0], alternate)
    return ("fixed-optional-source-damage-v1", (effect,), schema,
            tuple(dict.fromkeys((OPTIONAL_SOURCE_DAMAGE_MECHANIC, *mechanics, *fallback_mechanics))))
