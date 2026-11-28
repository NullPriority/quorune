"""Compile the exact two-target persistent copy instruction."""

from __future__ import annotations

from ..rules.persistent_public_copy import (
    PERSISTENT_PUBLIC_COPY_TEMPLATE, persistent_public_copy_effect, persistent_public_copy_target_schema,
)
from . import regex_working_set as re


def controlled_artifact_copy_activation_template(ability, text, *, public_timing):
    from ..rules.persistent_public_copy import (
        controlled_artifact_copy_effect, controlled_artifact_copy_price_is_closed,
        controlled_artifact_copy_target_schema,
    )
    from .activated_costs import activated_ability_cost

    if (public_timing is not None or ability.zones != ("battlefield",) or not ability.sorcery_speed
            or ability.mana_ability or ability.activation_conditions or ability.activation_limit is not None
            or not controlled_artifact_copy_price_is_closed(activated_ability_cost(ability))):
        return None
    material = " ".join(text.casefold().replace("’", "'").split())
    if material != ("target artifact you control becomes a copy of a second target artifact you control "
                    "until end of turn, except it isn't legendary."):
        return None
    return ("controlled-artifact-temporary-copy-v1", (controlled_artifact_copy_effect(),),
            controlled_artifact_copy_target_schema(), ("cr-115-targets",))


def persistent_public_copy_template(text: str):
    if re.fullmatch(
        r"Target artifact or creature becomes a copy of another target artifact or creature\.",
        " ".join(text.split()), re.IGNORECASE,
    ) is None:
        return None
    return (
        PERSISTENT_PUBLIC_COPY_TEMPLATE, (persistent_public_copy_effect(),),
        persistent_public_copy_target_schema(), ("cr-115-targets",),
    )
