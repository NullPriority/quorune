from __future__ import annotations

import re

from ..rules.source_references import SourceReferenceSpec
from ..source_tap_effect import SOURCE_TAP_EFFECT_FIELD
from .public_tap_state_set_templates import fixed_public_tap_state_set_effect_template
from .tap_state_templates import targeted_tap_state_effect_template


_DURATION = re.compile(
    r"(?P<body>.+?) for as long as (?P<source>.+?) remains tapped"
    r"(?P<land>\. It['’]s still a land\.)?\.?", re.IGNORECASE,
)
_SOURCE_KIND = re.compile(r"this (artifact|creature|enchantment|land|permanent|planeswalker|battle)", re.IGNORECASE)
_TARGETED = re.compile(
    r"(?P<tap>Tap (?:another )?target [^.]+)\. (?:It|That (?:creature|permanent)) "
    r"doesn['’]t untap during its controller['’]s untap step", re.IGNORECASE,
)
_OTHER_ARTIFACTS = re.compile(
    r"Tap all other artifacts\. They don['’]t untap during their controllers['’] untap steps", re.IGNORECASE,
)


def match_tapped_source_duration(text, *, card_name, source_context):
    if source_context.get("source_is_permanent") is not True or "remains tapped" not in text.casefold():
        return None
    match = _DURATION.fullmatch(text.strip())
    if match is None:
        return None
    source = match["source"]
    kind = _SOURCE_KIND.fullmatch(source)
    if kind is not None:
        if kind[1].casefold() not in {"permanent", *source_context.get("source_card_types", ())}:
            return None
    elif not SourceReferenceSpec(card_name).matches(source):
        return None
    return match


def tapped_source_untap_restraint_template(match):
    if match["land"] is not None:
        return None
    body = match["body"]
    single = _TARGETED.fullmatch(body)
    if single is not None:
        tap = targeted_tap_state_effect_template(single["tap"])
    elif _OTHER_ARTIFACTS.fullmatch(body) is not None:
        tap = fixed_public_tap_state_set_effect_template("Tap all other artifacts.")
    else:
        return None
    if tap is None:
        return None
    template, effects, schema, mechanics = tap.compiled()
    return f"{template}-source-untap-restraint", ({**effects[0], SOURCE_TAP_EFFECT_FIELD: True},), schema, mechanics
