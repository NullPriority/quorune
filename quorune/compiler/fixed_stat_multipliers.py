from __future__ import annotations

"""Closed Oracle grammar for fixed power and toughness multipliers."""

import re

from ..attachment_references import AttachmentReferenceKind
from ..creature_subtypes import canonical_creature_subtype_surface
from ..object_predicate import ObjectQuerySpec
from ..resolution_characteristic_model import CHARACTERISTIC_OPERATION
from ..rules.fixed_stat_multipliers import (
    ATTACK_MULTIPLIER_RECIPIENT, EQUIPPED_MULTIPLIER_RECIPIENT, STAT_MULTIPLIER_MECHANIC, STAT_MULTIPLIER_VERSION,
)
from ..rules.source_references import SourceReferenceSpec
from .direct_target import direct_permanent_target_spec


_STAT = r"power and toughness|power|toughness"
_BODY = re.compile(
    rf"(?P<action>double|triple) (?:the (?P<of_stat>{_STAT}) of (?P<of_subject>[^.,]+)"
    rf"|(?P<possessor>[^.,]+?)['’]s (?P<stat>{_STAT}))", re.I,
)
_CONTROLLED_SET = re.compile(r"each (?P<other>other )?(?P<kind>[^.,]+?) you control", re.I)
_ATTACK_RESULT = re.compile(rf"(?P<action>double|triple) its (?P<stat>{_STAT}) until end of turn\.?", re.I)
_FIRST_STRIKE_TAIL = " and it gains first strike"


def _compiled_multiplier(action, stat, selection, schema=None, *, first_strike=False):
    effect = {"op": CHARACTERISTIC_OPERATION, "schema_version": STAT_MULTIPLIER_VERSION,
        "factor": 2 if action.casefold() == "double" else 3, "stat": stat.casefold(), **selection}
    mechanics = ("cr-611-continuous-effects", STAT_MULTIPLIER_MECHANIC)
    if schema is not None:
        mechanics = (*mechanics, "cr-115-targets")
    if first_strike:
        effect["grant_first_strike"] = True
        mechanics = (*mechanics, "first strike")
    return "fixed-resolution-stat-multiplier-v1", (effect,), schema, mechanics


def attack_occurrence_multiplier_template(text):
    match = _ATTACK_RESULT.fullmatch(text.strip())
    if match is None:
        return None
    return _compiled_multiplier(match["action"], match["stat"], {"card": ATTACK_MULTIPLIER_RECIPIENT})


def fixed_stat_multiplier_effect_template(
    text, *, source_name, source_is_permanent, source_card_types, source_attachment_relation=None,
):
    body = text.strip().removesuffix(".")
    prefix, suffix = "until end of turn, ", " until end of turn"
    if body.casefold().startswith(prefix):
        body = body[len(prefix):]
    elif body.casefold().endswith(suffix):
        body = body[:-len(suffix)]
    else:
        return None
    first_strike = body.casefold().endswith(_FIRST_STRIKE_TAIL)
    if first_strike:
        body = body[:-len(_FIRST_STRIKE_TAIL)]
    match = _BODY.fullmatch(body)
    if match is None:
        return None
    subject = match["of_subject"] or match["possessor"]
    stat = (match["of_stat"] or match["stat"]).casefold()
    schema = None
    controlled = _CONTROLLED_SET.fullmatch(subject)
    if subject.casefold() in {"target creature", "target creature you control"}:
        schema = direct_permanent_target_spec(subject).to_target_schema()
        selection = {"card": "$target.0"}
    elif first_strike:
        return None
    elif subject.casefold() == "equipped creature" and source_attachment_relation is AttachmentReferenceKind.EQUIPPED:
        selection = {"card": EQUIPPED_MULTIPLIER_RECIPIENT.to_dict()}
    elif controlled is not None:
        kind = controlled["kind"]
        subtype = canonical_creature_subtype_surface(kind) if kind.casefold() != "creature" else None
        if subtype is None and kind.casefold() != "creature":
            return None
        selection = {"predicate": ObjectQuerySpec(zones=("battlefield",), types_all=("creature",),
            controller="$controller", subtypes_all=(subtype,) if subtype else ()).to_dict(),
            "exclude_source": controlled["other"] is not None}
    elif source_is_permanent is True and "creature" in source_card_types and (
        subject.casefold() == "this creature" or SourceReferenceSpec(source_name).matches(subject)
    ):
        selection = {"card": "$source.zone_object"}
    else:
        return None
    return _compiled_multiplier(match["action"], stat, selection, schema, first_strike=first_strike)
