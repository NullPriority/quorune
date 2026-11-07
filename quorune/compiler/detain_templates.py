from __future__ import annotations

"""Fixed Detain and printed next-turn action prohibitions."""

import re

from ..replacement.immutable import thaw_value
from ..rules.detain import (
    DETAIN_OPERATION, DIRECT_PROHIBITION_OPERATION, DIRECT_PROHIBITION_TARGET, detain_target_schema,
)


_DETAIN_TARGET = re.compile(
    r"Detain (?:target (?P<single>creature|nonland permanent) an opponent controls|"
    r"up to two target (?P<group>creatures|nonland permanents) your opponents control)\.?",
    re.IGNORECASE,
)
_DETAIN_SET = re.compile(
    r"Detain each nonland permanent your opponents control with mana value (?P<maximum>0|[1-9][0-9]{0,2}) or less\.?",
    re.IGNORECASE,
)
_DIRECT_PROHIBITION = re.compile(
    r"Until your next turn, target creature can['’]t attack or block "
    r"and its activated abilities can['’]t be activated\.?",
    re.IGNORECASE,
)


def fixed_detain_effect_template(text: str):
    body = text.strip()
    targeted = _DETAIN_TARGET.fullmatch(body)
    if targeted is not None:
        subject = targeted["single"] or targeted["group"]
        schema = detain_target_schema(creatures_only=subject.casefold().startswith("creature"),
                                      up_to_two=targeted["group"] is not None)
        return ("fixed-detain-targets-v1", ({"op": DETAIN_OPERATION, "schema_version": 1, "cards": "$targets"},),
                schema, ("cr-115-targets",))
    selection = _DETAIN_SET.fullmatch(body)
    if selection is not None:
        return ("fixed-detain-opponents-set-v1", ({"op": DETAIN_OPERATION, "schema_version": 1,
                "maximum_mana_value": int(selection["maximum"])},), None, ())
    return None


def fixed_direct_action_prohibition_template(text: str):
    if not _DIRECT_PROHIBITION.fullmatch(text.strip()):
        return None
    return ("fixed-next-turn-action-prohibition-v1",
            ({"op": DIRECT_PROHIBITION_OPERATION, "schema_version": 1, "card": "$target.0"},),
            thaw_value(DIRECT_PROHIBITION_TARGET), ("cr-115-targets",))
