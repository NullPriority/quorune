from __future__ import annotations

"""Unrestricted cardinality over complete existing homogeneous result owners."""

import re
from copy import deepcopy

from ..target_cardinality import AnnouncedTargetCardinality, selected_target_count_descriptor
from .fixed_homogeneous_target_sets import fixed_homogeneous_target_set_effect_template
from .target_characteristic_sets import target_characteristic_set_template

ANY_NUMBER_TARGET_MECHANIC = "any-number-target-set"


def any_number_target_set_template(text):
    if len(re.findall(r"\bany number of (?:other )?target\b", text, re.I)) != 1:
        return None
    prototype = re.sub(r"\bany number of (?P<other>other )?target\b",
        lambda match: "up to one " + (match["other"] or "") + "target", text, flags=re.I)
    template = fixed_homogeneous_target_set_effect_template(prototype)
    compiled = template.compiled() if template is not None else target_characteristic_set_template(prototype)
    if compiled is None or len(compiled[1]) != 1:
        return None
    effect = deepcopy(dict(compiled[1][0]))
    schema = deepcopy(dict(compiled[2]))
    if schema.pop("up_to", None) != 1 or effect.get("maximum_targets") != 1:
        return None
    schema["count_parameter"] = AnnouncedTargetCardinality(
        selection="up_to", parameter="any_number", schema_version=2).to_dict()
    effect.update(maximum_targets=selected_target_count_descriptor(), announced_target_count=True)
    return (compiled[0] + "-any-number-v1", (effect,), schema,
        tuple(dict.fromkeys((*compiled[3], ANY_NUMBER_TARGET_MECHANIC))))
