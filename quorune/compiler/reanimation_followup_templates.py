from __future__ import annotations

"""Compose a targeted return with one complete linked end-step instruction."""

from dataclasses import dataclass, replace
import re

from ..rules.reanimation_followup import (
    BattlefieldEntryFollowup, REANIMATION_EXILE_REPLACEMENT_VERSION, REANIMATION_FOLLOWUP_MECHANIC,
    REANIMATION_FOLLOWUP_VERSION,
)
from .reanimation_templates import FixedTargetReanimationTemplate, fixed_target_reanimation_effect_template


_HASTE = re.compile(r"(?P<subject>It|That creature|That artifact) (?P<boost>gets \+2/\+0 and )?gains haste"
    r"(?P<temporary> until end of turn)?", re.I)
_DEPARTURE = (r"(?P<action>exile it|sacrifice (?P<sacrificed>it|(?:the|that) (?:creature|artifact))"
    r"|return it to its owner['’]s hand)")
_END_STEP = r"(?P<timing>the|your) next end step"
_DELAYED = (
    re.compile(rf"At the beginning of {_END_STEP}, {_DEPARTURE}\.?", re.I),
    re.compile(rf"{_DEPARTURE} at the beginning of {_END_STEP}\.?", re.I),
)
_EXILE_ON_LEAVE = re.compile(
    r" If (?P<subject>it|that creature) would leave the battlefield, exile it instead of putting it anywhere else\.?$",
    re.I,
)


@dataclass(frozen=True, slots=True)
class FixedReanimationFollowupTemplate:
    base: FixedTargetReanimationTemplate
    followup: BattlefieldEntryFollowup

    def compiled(self):
        template, effects, schema, mechanics = self.base.compiled()
        version = REANIMATION_EXILE_REPLACEMENT_VERSION if self.followup.exile_on_leave else REANIMATION_FOLLOWUP_VERSION
        effect = {**effects[0], "schema_version": version, "followup": self.followup.to_dict()}
        return (f"{template}-haste-departure-v{version}", (effect,), schema, (*mechanics, REANIMATION_FOLLOWUP_MECHANIC))


def fixed_battlefield_entry_followup(text, *, receiver_types):
    haste_text, separator, departure_text = text.partition(". ")
    haste = _HASTE.fullmatch(haste_text)
    if not separator or haste is None:
        return None
    delayed = _DELAYED[0].fullmatch(departure_text) or _DELAYED[1].fullmatch(departure_text)
    if delayed is None:
        return None
    for subject in (haste["subject"], delayed["sacrificed"]):
        if subject is not None and subject.casefold().split()[-1] not in {"it", *receiver_types}:
            return None
    action = delayed["action"].split()[0].casefold()
    try:
        return BattlefieldEntryFollowup(
            haste_duration="until_end_of_turn" if haste["temporary"] else "zone_object",
            departure="bounce" if action == "return" else action,
            end_step="controllers_next" if delayed["timing"].casefold() == "your" else "next",
            power_boost=2 if haste["boost"] else 0,
        )
    except ValueError:
        return None


def fixed_reanimation_followup_template(text):
    base_text, separator, tail = text.strip().partition(". ")
    if not separator or "haste" not in tail.casefold():
        return None
    base = fixed_target_reanimation_effect_template(base_text)
    if base is None:
        return None
    receiver_types = set(base.target_schema.get("types_all", ()))
    alternatives = base.target_schema.get("types_any", ())
    if len(alternatives) == 1:
        receiver_types.update(alternatives)
    replacement = _EXILE_ON_LEAVE.search(tail)
    if replacement:
        if ("min" in base.target_schema
                or replacement["subject"].casefold() == "that creature" and "creature" not in receiver_types):
            return None
        tail = tail[:replacement.start()]
    followup = fixed_battlefield_entry_followup(tail, receiver_types=receiver_types)
    if followup is None:
        return None
    if replacement:
        try:
            followup = replace(followup, exile_on_leave=True)
        except ValueError:
            return None
    return FixedReanimationFollowupTemplate(base, followup)
