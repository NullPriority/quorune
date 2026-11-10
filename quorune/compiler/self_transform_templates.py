from __future__ import annotations

"""Source-pinned Transform instructions through the existing owner."""

import re
from ..rules.source_references import SourceReferenceSpec
from ..rules.transform_capability_shapes import TRIGGERED_SOURCE_TRANSFORM_MECHANIC

SELF_TRANSFORM_MECHANIC = "fixed-activated-source-transform"


def fixed_self_transform_effect_template(text, *, card_name, triggered=False):
    subjects = "this creature|this artifact|this permanent"
    if triggered:
        subjects += "|this enchantment"
    reference = SourceReferenceSpec(card_name).regex_pattern
    if re.fullmatch(rf"Transform (?:{subjects}|{reference})\.?", text.strip(), re.I) is None:
        return None
    mechanic = TRIGGERED_SOURCE_TRANSFORM_MECHANIC if triggered else SELF_TRANSFORM_MECHANIC
    effect = {"op": "transform", "card": "$source.zone_object",
              "expected_transform_count": "$source.transform_count"}
    return (f"{mechanic}-v1", (effect,), None, (mechanic, "cr-701-transform"))
