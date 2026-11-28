from __future__ import annotations

from . import regex_working_set as re

from ..rules.source_references import SourceReferenceSpec
from ..source_tap_effect import (
    OPTIONAL_SOURCE_CONTROL_UNTAP_VERSION, SOURCE_CONTROL_UNTAP_FIELD, SOURCE_TAP_EFFECT_FIELD,
)
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
_CONTROLLED_SOURCE_TARGET = re.compile(
    r"(?P<tap>Tap target (?:red or green )?creature an opponent controls)\. "
    r"That creature doesn['’]t untap during its controller['’]s untap step "
    r"for as long as you control this creature\.?",
    re.IGNORECASE,
)
_OPTIONAL_CONTROLLED_SOURCE_TARGET = re.compile(
    r"Tap up to one target creature\. (?:It|That creature) doesn['’]t untap during its controller['’]s untap step "
    r"for as long as you control (?P<source>[^.]+)\.?",
    re.IGNORECASE,
)
_CONTROLLED_SAGA_TARGET = re.compile(
    r"(?P<tap>Tap target creature an opponent controls)\. "
    r"It doesn['’]t untap during its controller['’]s untap step "
    r"for as long as you control this Saga\.?",
    re.IGNORECASE,
)
_OTHER_ARTIFACTS = re.compile(
    r"Tap all other artifacts\. They don['’]t untap during their controllers['’] untap steps", re.IGNORECASE,
)


def _controlled_source_untap_restraint(tap_text):
    tap = targeted_tap_state_effect_template(tap_text)
    if tap is None:
        return None
    template, effects, schema, mechanics = tap.compiled()
    return (
        f"{template}-controlled-source-untap-restraint-v1",
        ({**effects[0], SOURCE_CONTROL_UNTAP_FIELD: 1},), schema, mechanics,
    )


def controlled_source_untap_restraint_template(text, *, source_context, card_name=None):
    if (source_context.get("source_is_permanent") is not True
            or "creature" not in source_context.get("source_card_types", ())):
        return None
    body = " ".join(text.split())
    match = _CONTROLLED_SOURCE_TARGET.fullmatch(body)
    if match is not None:
        return _controlled_source_untap_restraint(match["tap"])
    optional = _OPTIONAL_CONTROLLED_SOURCE_TARGET.fullmatch(body)
    if optional is None:
        return None
    reference = optional["source"]
    if reference.casefold() != "this creature" and (
        card_name is None or not SourceReferenceSpec(card_name).matches(reference)
    ):
        return None
    template = _controlled_source_untap_restraint("Tap target creature")
    if template is None:
        return None
    label, effects, schema, mechanics = template
    schema.pop("count")
    schema["up_to"] = 1
    return (
        f"{label}-optional-target", ({**effects[0], SOURCE_CONTROL_UNTAP_FIELD: OPTIONAL_SOURCE_CONTROL_UNTAP_VERSION},),
        schema, mechanics,
    )


def controlled_saga_untap_restraint_template(text):
    match = _CONTROLLED_SAGA_TARGET.fullmatch(" ".join(text.split()))
    return _controlled_source_untap_restraint(match["tap"]) if match is not None else None


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
