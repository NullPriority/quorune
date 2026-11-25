"""Match an activated graveyard exile paired with a persistent copy."""

from __future__ import annotations

from ..rules.source_references import SourceReferenceSpec, source_self_permanent_pattern
from ..rules.graveyard_exile_copy import (
    GRAVEYARD_EXILE_COPY_TEMPLATE, graveyard_exile_copy_effect, graveyard_exile_copy_target_schema,
    is_graveyard_exile_copy_cost,
)
from . import regex_working_set as re
from .activated_costs import activated_ability_cost


def matches_graveyard_exile_self_copy(effect: str, *, source_name: str) -> bool:
    """Match the whole result, including the retained activation."""
    source = SourceReferenceSpec(source_name).regex_pattern
    receiver = rf"(?:{source}|{source_self_permanent_pattern()})"
    return re.fullmatch(
        rf"Exile target creature card from a graveyard\. {receiver} becomes a copy of that card, "
        r"except it has this ability\.",
        " ".join(effect.split()),
        re.IGNORECASE,
    ) is not None


def graveyard_exile_self_copy_template(ability, *, source_name, text, public_timing):
    if (ability.cost_text != "{1}{U}{B}" or ability.zones != ("battlefield",) or public_timing is not None
            or ability.sorcery_speed or ability.activation_conditions or ability.activation_limit is not None):
        return None
    if not (matches_graveyard_exile_self_copy(text, source_name=source_name)
            and is_graveyard_exile_copy_cost(activated_ability_cost(ability))):
        return None
    return (
        GRAVEYARD_EXILE_COPY_TEMPLATE, (graveyard_exile_copy_effect(),),
        graveyard_exile_copy_target_schema(), ("cr-115-targets",),
    )
