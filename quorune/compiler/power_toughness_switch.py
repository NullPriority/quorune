from __future__ import annotations

"""Source-pinned recipient grammar for temporary power and toughness switches."""

import re

from ..resolution_characteristic_model import CHARACTERISTIC_OPERATION
from ..rules.power_toughness_switch import (
    SWITCH_CREATURE_SET, SWITCH_MECHANIC, SWITCH_SCHEMA_VERSION, switch_target_schema,
)
from ..rules.source_references import SourceReferenceSpec
from ..target_cardinality import AnnouncedTargetCardinality, selected_target_count_descriptor
from .any_number_target_sets import ANY_NUMBER_TARGET_MECHANIC


_SCALAR = re.compile(r"Switch (?P<subject>.+?)['’]s power and toughness until end of turn\.?", re.I)
_SET = re.compile(r"Switch the power and toughness of each of any number of "
    r"target creatures until end of turn\.?", re.I)


def _switch_template(selection, target_schema=None, *, any_number=False):
    mechanics = ("cr-611-continuous-effects", SWITCH_MECHANIC,
        *(("cr-115-targets",) if target_schema is not None else ()),
        *((ANY_NUMBER_TARGET_MECHANIC,) if any_number else ()))
    effect = {"op": CHARACTERISTIC_OPERATION, "schema_version": SWITCH_SCHEMA_VERSION, **selection}
    return ("fixed-power-toughness-switch-v1", (effect,), target_schema, mechanics)


def power_toughness_switch_effect_template(text, *, source_name, source_is_permanent, source_card_types):
    fixed = _SCALAR.fullmatch(text.strip())
    if fixed is not None:
        subject = fixed["subject"]
        if subject.casefold() == "target creature":
            return _switch_template({"card": "$target.0"}, switch_target_schema())
        if subject.casefold() == "each creature":
            return _switch_template({"predicate": SWITCH_CREATURE_SET.to_dict()})
        if source_is_permanent is True and "creature" in source_card_types and (
            subject.casefold() == "this creature" or SourceReferenceSpec(source_name).matches(subject)
        ):
            return _switch_template({"card": "$source.zone_object"})
        return None
    if _SET.fullmatch(text.strip()) is None:
        return None
    schema = switch_target_schema()
    del schema["count"]
    schema["count_parameter"] = AnnouncedTargetCardinality(
        selection="up_to", parameter="any_number", schema_version=2).to_dict()
    return _switch_template({"cards": "$targets", "maximum_targets": selected_target_count_descriptor(),
        "announced_target_count": True}, schema, any_number=True)
