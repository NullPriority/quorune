from __future__ import annotations

"""Closed resolution-created control over one existing permanent target."""

from dataclasses import dataclass, replace
from . import regex_working_set as re
from typing import Any, Mapping

from ..continuous_effect_model import ContinuousEffectDuration
from ..control_exchange_model import PERMANENT_CONTROL_EXCHANGE_MECHANIC, PermanentControlExchange
from ..object_predicate import ObjectQuerySpec
from ..rules.source_references import SourceReferenceSpec, source_self_permanent_type
from .direct_target import DirectPermanentTargetSpec, direct_permanent_target_spec
from .fixed_resolution_characteristic_queries import fixed_resolution_characteristic_query_is_closed
from .public_state_queries import fixed_characteristic_battlefield_query_subject


FIXED_CONTROL_MECHANIC = "fixed-resolution-control"
FIXED_CONTROL_CAPABILITY = "continuous.control.fixed_resolution"
FIXED_CONTROL_OPERATION = "gain_control"
_CONTROL = re.compile(
    r"(?:you )?gain control of (?P<subject>target .+?)"
    r"(?P<temporary> until end of turn)?\.?",
    re.IGNORECASE,
)
_UNTAP_CONTROL = re.compile(
    r"untap (?P<subject>target .+?) and gain control of it"
    r"(?P<temporary> until end of turn)?\.?",
    re.IGNORECASE,
)
_SOURCE_CONTROL = re.compile(
    r"(?:you )?gain control of (?P<subject>target .+?) for as long as "
    r"(?:(?P<control>you control) (?P<controlled_source>.+?)|"
    r"(?P<present_source>.+?) remains on the battlefield)\.?",
    re.IGNORECASE,
)
_TAPPED_SOURCE_CONTROL = re.compile(
    r"(?:you )?gain control of (?P<subject>target .+?) for as long as "
    r"(?:(?P<control>you control) (?P<controlled_source>.+?) and )?"
    r"(?P<tapped_source>.+?) remains tapped\.?", re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class FixedControlTemplate:
    target: DirectPermanentTargetSpec
    duration: ContinuousEffectDuration
    untap_first: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.target, DirectPermanentTargetSpec):
            raise ValueError("Fixed control requires a typed permanent target")
        if not isinstance(self.duration, ContinuousEffectDuration) or self.duration not in {
            ContinuousEffectDuration.UNTIL_END_OF_TURN,
            ContinuousEffectDuration.ZONE_OBJECT,
            ContinuousEffectDuration.UNTIL_SOURCE_LEAVES,
            ContinuousEffectDuration.UNTIL_SOURCE_CONTROL_CHANGES,
            ContinuousEffectDuration.UNTIL_SOURCE_LEAVES_OR_UNTAPS,
            ContinuousEffectDuration.UNTIL_SOURCE_CONTROL_CHANGES_OR_UNTAPS,
        }:
            raise ValueError("Fixed control duration is unsupported")
        if type(self.untap_first) is not bool:
            raise ValueError("Fixed control instruction order must be explicit")

    def compiled(self) -> tuple[
        str, tuple[Mapping[str, Any], ...], Mapping[str, Any], tuple[str, ...]
    ]:
        control = {
                "op": FIXED_CONTROL_OPERATION,
                "card": "$target.0",
                "controller": "$controller",
                "duration": self.duration.value,
                "source": "$source",
            }
        if self.duration.source_bound:
            control["duration_source"] = "$source.zone_object"
        return (
            f"fixed-control-{self.duration.value}-{self.target.slug}"
            f"{'-untap-first' if self.untap_first else ''}-v1",
            (*(({"op": "untap", "card": "$target.0"},) if self.untap_first else ()), control),
            self.target.to_target_schema(),
            (FIXED_CONTROL_MECHANIC, "cr-115-targets", "cr-611-continuous-effects",
             *(("tap-and-untap",) if self.untap_first else ())),
        )


def fixed_control_effect_template(
    text: str, *, card_name: str = "source", source_is_permanent: bool | None = None,
    source_card_types: tuple[str, ...] = (),
) -> FixedControlTemplate | None:
    match = _TAPPED_SOURCE_CONTROL.fullmatch(text.strip())
    tapped = match is not None
    if match is None:
        match = _SOURCE_CONTROL.fullmatch(text.strip())
    guarded = match is not None
    untap_first = False
    if guarded:
        if source_is_permanent is not True:
            return None
        subjects = (match["controlled_source"], match["tapped_source"] if tapped else match["present_source"])
        for subject in (value for value in subjects if value is not None):
            source_kind = source_self_permanent_type(subject)
            if not SourceReferenceSpec(card_name).matches(subject) and not (
                source_kind == "permanent" or source_kind in source_card_types
            ):
                return None
    else:
        match = _CONTROL.fullmatch(text.strip())
        if match is None:
            match = _UNTAP_CONTROL.fullmatch(text.strip())
            untap_first = True
    if match is None:
        return None
    target = direct_permanent_target_spec(match["subject"])
    if target is None:
        return None
    return FixedControlTemplate(
        target=target,
        duration=(
            (ContinuousEffectDuration.UNTIL_SOURCE_CONTROL_CHANGES_OR_UNTAPS if match["control"]
             else ContinuousEffectDuration.UNTIL_SOURCE_LEAVES_OR_UNTAPS) if tapped else
            (ContinuousEffectDuration.UNTIL_SOURCE_CONTROL_CHANGES if match["control"]
             else ContinuousEffectDuration.UNTIL_SOURCE_LEAVES) if guarded else
            ContinuousEffectDuration.UNTIL_END_OF_TURN
            if match["temporary"] else ContinuousEffectDuration.ZONE_OBJECT
        ),
        untap_first=untap_first,
    )


@dataclass(frozen=True, slots=True)
class FixedControlSetTemplate:
    predicate: ObjectQuerySpec
    duration: ContinuousEffectDuration
    steps: tuple[str, ...]

    def compiled(self) -> tuple[str, tuple[Mapping[str, Any], ...], None, tuple[str, ...]]:
        return (
            "fixed-control-public-set-v1",
            ({"op": "gain_control_set", "predicate": self.predicate.to_dict(),
              "controller": "$controller", "duration": self.duration.value,
              "source": "$source", "steps": list(self.steps)},),
            None,
            (FIXED_CONTROL_MECHANIC, "cr-611-continuous-effects",
             *(("tap-and-untap",) if "untap" in self.steps else ()),
             *(("haste",) if "haste" in self.steps else ())),
        )


TWO_CONTROLLER_CREATURE_TRANSFER_TEMPLATE = "two-controller-creature-untap-transfer-haste-v1"
_TWO_CONTROLLER_CREATURE_TRANSFER_TEXT = (
    "Untap all creatures you control and all creatures target opponent controls. "
    "You and that opponent each gain control of all creatures the other controls until end of turn. "
    "Those creatures gain haste until end of turn."
)


def fixed_two_controller_creature_transfer_template(text):
    if " ".join(text.split()).casefold() != _TWO_CONTROLLER_CREATURE_TRANSFER_TEXT.casefold():
        return None
    from ..recipient_control_model import two_controller_creature_transfer_effect, two_controller_creature_transfer_target

    return (TWO_CONTROLLER_CREATURE_TRANSFER_TEMPLATE,
            (two_controller_creature_transfer_effect(),), two_controller_creature_transfer_target(),
            (FIXED_CONTROL_MECHANIC, "cr-611-continuous-effects", "cr-115-targets", "tap-and-untap", "haste"))


def fixed_control_set_effect_template(text: str) -> FixedControlSetTemplate | None:
    normalized = " ".join(text.strip().split())
    match = re.fullmatch(
        r"(?P<untap>untap (?P<untap_subject>all .+?) and )?(?:you )?gain control of "
        r"(?P<subject>all .+?|them)(?P<temporary> until end of turn)?\."
        r"(?P<after>.*)", normalized, re.IGNORECASE,
    )
    if match is None:
        return None
    untap_first = match["untap"] is not None
    if (match["subject"].casefold() == "them") is not untap_first:
        return None
    subject = (match["untap_subject"] if untap_first else match["subject"])[4:]
    if subject.casefold() == "permanents you own":
        query = ObjectQuerySpec(zones=("battlefield",), owner="$controller")
    else:
        parsed = fixed_characteristic_battlefield_query_subject(subject)
        if parsed is None:
            return None
        relation, query, excluded = parsed
        if excluded:
            return None
        if relation == "source_controller":
            query = replace(query, controller="$controller")
        elif relation == "source_opponents":
            query = replace(query, excluded_controllers=("$controller",))
        elif relation != "any":
            return None
        if not fixed_resolution_characteristic_query_is_closed(query, target_schema=None):
            return None
    steps = ["untap"] if untap_first else []
    steps.append("gain_control")
    after = match["after"].strip()
    untap = re.match(r"Untap (?:them|those creatures)\.\s*", after, re.IGNORECASE)
    if untap is not None:
        if untap_first:
            return None
        steps.append("untap")
        after = after[untap.end():]
    if after:
        if not match["temporary"] or re.fullmatch(
            r"(?:They|Those creatures) gain haste until end of turn\.", after, re.IGNORECASE,
        ) is None:
            return None
        steps.append("haste")
    return FixedControlSetTemplate(
        predicate=query,
        duration=(ContinuousEffectDuration.UNTIL_END_OF_TURN
                  if match["temporary"] else ContinuousEffectDuration.ZONE_OBJECT),
        steps=tuple(steps),
    )



_CONTROL_EXCHANGES = {
    "exchange control of two target creatures.": PermanentControlExchange.CREATURE,
    "exchange control of two target artifacts.": PermanentControlExchange.ARTIFACT,
    "exchange control of two target enchantments.": PermanentControlExchange.ENCHANTMENT,
    "exchange control of two target lands.": PermanentControlExchange.LAND,
    "exchange control of two target permanents that share a permanent type.":
        PermanentControlExchange.SHARED_PERMANENT_TYPE,
    "exchange control of target artifact or creature and another target permanent that shares one of those types with it.":
        PermanentControlExchange.SHARED_ARTIFACT_OR_CREATURE_TYPE,
    "exchange control of target land you control and target land an opponent controls.":
        PermanentControlExchange.CONTROLLER_AND_OPPONENT_LAND,
}


def permanent_control_exchange_template(text, *, source_is_permanent=None, source_card_types=()):
    material = " ".join(text.split()).casefold()
    scope = _CONTROL_EXCHANGES.get(material.removesuffix(" (this effect lasts indefinitely.)"))
    if (scope is None and material == "exchange control of this artifact and target nonland permanent."
            and source_is_permanent is True and "artifact" in source_card_types):
        scope = PermanentControlExchange.SOURCE_AND_NONLAND
    if scope is None:
        return None
    return (
        f"fixed-permanent-control-exchange-{scope.value}-v1", (scope.to_effect(),), scope.target_schema(),
        (PERMANENT_CONTROL_EXCHANGE_MECHANIC, "cr-115-targets", "cr-611-continuous-effects"),
    )
