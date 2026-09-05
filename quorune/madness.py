from __future__ import annotations

"""Typed ordinary fixed-mana Madness declarations."""

import re
from typing import Any

from .card_programs.admission import REQUIRES_COMPLETE_CARD_PROGRAM_FIELD
from .cast_lifecycles import (
    compile_fixed_cast_lifecycle,
    FixedCastLifecycleKind,
    FixedCastLifecycleSpec,
)


MADNESS_CAPABILITY_ID = "casting.madness.fixed_mana"
MADNESS_DISCARD_CAPABILITY_ID = "zone.discard.typed_cause"
MADNESS_REPLACEMENT_HANDLER_ID = "replacement.zone.madness-discard.v1"
MADNESS_TRIGGER_HANDLER_ID = "casting.madness.fixed-mana-trigger.v1"
MADNESS_REPLACEMENT_TEMPLATE_ID = "madness-discard-replacement-v1"
MADNESS_TRIGGER_TEMPLATE_ID = "madness-cast-trigger-v1"
MADNESS_REPLACEMENT_EVENT = "zone.change"
MADNESS_TRIGGER_EVENT = "card.discarded.self"
MADNESS_CHOICE_OPERATION = "madness_cast_choice"

_ORDINARY_COST = r"(?:\{(?:0|[1-9][0-9]*|[WUBRGC])\})+"
_REMINDER = (
    r"If you discard this card, discard it into exile\. When you do, "
    r"cast it for its madness cost or put it into your graveyard\."
)
_FIXED_MADNESS = re.compile(
    rf"^Madness (?P<cost>{_ORDINARY_COST})(?: \({_REMINDER}\))?\.?$",
    re.IGNORECASE,
)


def fixed_madness_spec(
    *,
    material_line: str,
    oracle_line: str,
    line_index: int,
) -> FixedCastLifecycleSpec | None:
    """Compile only ordinary fixed-mana Madness and its exact reminder."""

    if _FIXED_MADNESS.fullmatch(" ".join(oracle_line.strip().split())) is None:
        return None
    spec = compile_fixed_cast_lifecycle(
        material_line=material_line,
        oracle_line=oracle_line,
        line_index=line_index,
    )
    return (
        spec
        if spec is not None and spec.kind is FixedCastLifecycleKind.MADNESS
        else None
    )


def madness_replacement_handler_descriptor(
    spec: FixedCastLifecycleSpec,
) -> dict[str, Any]:
    return {
        "handler_id": MADNESS_REPLACEMENT_HANDLER_ID,
        "schema_version": 1,
        "event": MADNESS_REPLACEMENT_EVENT,
        REQUIRES_COMPLETE_CARD_PROGRAM_FIELD: True,
        "madness": spec.to_dict(),
    }


def madness_trigger_handler_descriptor(
    spec: FixedCastLifecycleSpec,
) -> dict[str, Any]:
    return {
        "handler_id": MADNESS_TRIGGER_HANDLER_ID,
        "schema_version": 1,
        "event": MADNESS_TRIGGER_EVENT,
        REQUIRES_COMPLETE_CARD_PROGRAM_FIELD: True,
        "madness": spec.to_dict(),
    }


__all__ = [
    "fixed_madness_spec",
    "MADNESS_CAPABILITY_ID",
    "MADNESS_CHOICE_OPERATION",
    "MADNESS_DISCARD_CAPABILITY_ID",
    "MADNESS_REPLACEMENT_EVENT",
    "MADNESS_REPLACEMENT_HANDLER_ID",
    "MADNESS_REPLACEMENT_TEMPLATE_ID",
    "MADNESS_TRIGGER_EVENT",
    "MADNESS_TRIGGER_HANDLER_ID",
    "MADNESS_TRIGGER_TEMPLATE_ID",
    "madness_replacement_handler_descriptor",
    "madness_trigger_handler_descriptor",
]
