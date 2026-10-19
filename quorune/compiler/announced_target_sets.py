from __future__ import annotations

"""Mana-announced X target sets over existing single-target productions."""

from copy import deepcopy
import re
from typing import Any

from ..query_effect_amount_model import CastXAmountSpec
from ..target_cardinality import AnnouncedTargetCardinality, ANNOUNCED_TARGET_SET_CAPABILITY
from .fixed_homogeneous_target_sets import (
    _compiled_singular, _OPERATION_LIFT, _singularize_subject,
)


ANNOUNCED_TARGET_SET_MECHANIC = "mana-announced-target-set"
_PATTERN = re.compile(
    r"(?P<verb>destroy|exile|tap|untap|return) (?P<up_to>up to )?X "
    r"(?P<other>other )?target (?P<subject>[^.]+)\.?", re.IGNORECASE,
)


def announced_target_set_effect_template(text: str) -> Any:
    """Lower cardinality only; the caller must establish mana-announced X."""

    match = _PATTERN.fullmatch(" ".join(text.strip().split()))
    if match is None:
        return None
    verb = match["verb"].casefold()
    subject = match["subject"]
    singular = (verb + (" another target " if match["other"] else " target ")
        + _singularize_subject(subject) + ".")
    compiled = _compiled_singular(verb, singular)
    if compiled is None or len(compiled[1]) != 1:
        return None
    effect = compiled[1][0]
    if (set(effect) != {"op", "card"} or effect.get("card") != "$target.0"
            or effect.get("op") not in _OPERATION_LIFT):
        return None
    schema = deepcopy(dict(compiled[2]))
    if schema.pop("count", None) != 1:
        return None
    count = AnnouncedTargetCardinality(selection="up_to" if match["up_to"] else "exact")
    schema["count_parameter"] = count.to_dict()
    if "from a single graveyard" in subject.casefold():
        if effect["op"] != "exile_public_graveyard_card":
            return None
        schema["same_owner"] = True
    lifted = {"op": _OPERATION_LIFT[effect["op"]], "cards": "$targets",
        "maximum_targets": CastXAmountSpec().to_dict(), "announced_target_count": True}
    return (compiled[0] + "-mana-x-target-set-v1", (lifted,), schema,
        tuple(dict.fromkeys((*compiled[3], ANNOUNCED_TARGET_SET_MECHANIC, "cr-115-targets"))))


def announced_target_clause_compiler(compile_atomic):
    """Compose the new target leaf with independently owned atomic results."""
    def compile_clause(text):
        atomic = compile_atomic(text)
        if atomic[0] is not None:
            return atomic
        return announced_target_set_effect_template(text) or atomic
    return compile_clause
