"""Lower one original player's optional damage choice and fixed alternate result."""

from . import regex_working_set as re
from .damage_templates import fixed_damage_effect_template
from .destruction_templates import targeted_destruction_effect_template
from .fixed_numbers import FIXED_COUNT_PATTERN, fixed_number
from ..random_discard import random_discard_effect_template
from ..rules.optional_source_damage import OPTIONAL_SOURCE_DAMAGE_MECHANIC, source_damage_choice
from ..rules.source_references import SourceReferenceSpec
from ..target_controller_reference import TARGET_CONTROLLER_INFORMATION_MECHANIC, target_controller_reference


_DRAW = re.compile(
    rf"Target opponent may have (?P<source>.+?) deal (?P<damage>[1-9][0-9]*) damage to them\. "
    rf"If that player doesn['’]t, you draw (?P<count>{FIXED_COUNT_PATTERN}) cards?\.", re.I,
)
_DISCARD = re.compile(
    r"(?P<alternate>Target player discards? .+? at random) unless that player has "
    r"(?P<source>.+?) deal (?P<damage>[1-9][0-9]*) damage to them\.", re.I,
)


_DESTROY = re.compile(
    r"(?P<alternate>Destroy target nonbasic land) unless its controller has "
    r"(?P<source>.+?) deal (?P<damage>[1-9][0-9]*) damage to them\.", re.I,
)


def optional_source_damage_template(text, *, card_name, source_is_permanent):
    if source_is_permanent is not False:
        return None
    body = " ".join(text.split())
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
