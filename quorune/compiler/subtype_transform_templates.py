from __future__ import annotations

"""Closed unqualified mass-transform grammar with a fixed subtype."""

from . import regex_working_set as re
from ..creature_subtypes import canonical_creature_subtype_surface
from ..rules.subtype_transform import SubtypeTransformSpec, SUBTYPE_TRANSFORM_MECHANIC


def fixed_subtype_transform_effect_template(text):
    match = re.fullmatch(r"Transform all ([A-Za-z][A-Za-z'-]*)\.?", text.strip(), re.I)
    if match is None:
        return None
    subtype = canonical_creature_subtype_surface(match.group(1))
    if subtype is None:
        return None
    return ("fixed-subtype-transform-v1", (SubtypeTransformSpec(subtype).to_effect(),),
        None, (SUBTYPE_TRANSFORM_MECHANIC, "cr-701-transform"))
