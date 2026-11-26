from __future__ import annotations

"""Closed resolution-created control over one existing permanent target."""

from dataclasses import dataclass, replace
from . import regex_working_set as re
from typing import Any, Mapping

from ..continuous_effect_model import ContinuousEffectDuration
from ..control_exchange_model import PERMANENT_CONTROL_EXCHANGE_MECHANIC, PermanentControlExchange
from ..entry_control_exchange_model import entry_exchange_event_condition
from ..object_predicate import ObjectQuerySpec
from ..rules.source_references import SourceReferenceSpec, source_self_permanent_type
from ..target_numeric import TargetNumericCharacteristic, TargetNumericCharacteristicSpec, TargetNumericComparison
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


_ATTACHED_TEMPORARY_CONTROL_BODY = (
    "gain control of enchanted permanent until end of turn. untap that permanent. it gains haste until end of turn."
)


def is_attached_temporary_control_body(text: str) -> bool:
    return " ".join(text.split()).casefold() == _ATTACHED_TEMPORARY_CONTROL_BODY


def attached_temporary_control_trigger_result(binding, body):
    from ..rules.attached_temporary_control import (
        ATTACHED_TEMPORARY_CONTROL_MECHANICS, attached_temporary_control_condition, attached_temporary_control_effect,
    )

    if (binding.variant != "independent_dual_event" or binding.active_zone != "battlefield"
            or binding.event_condition != attached_temporary_control_condition()
            or body != binding.body or not is_attached_temporary_control_body(body)):
        return None
    return ("attached-permanent-temporary-control-v1", (attached_temporary_control_effect(),),
            None, ATTACHED_TEMPORARY_CONTROL_MECHANICS), False


_TARGETED_CONTROL_PREFIX = "gain control of any number of target creatures"
_OPPONENT_CONTROL_PREFIX = "for each opponent, gain control of up to one target creature that player controls"
_TARGETED_CONTROL_SUFFIX = " until end of turn. untap those creatures. they gain haste until end of turn."


def targeted_control_set_template(text: str, *, per_opponent: bool = False):
    from ..rules.targeted_control_set import (
        TARGETED_CONTROL_SET_MECHANICS, targeted_control_set_effect, targeted_control_set_schema,
    )

    prefix = _OPPONENT_CONTROL_PREFIX if per_opponent else _TARGETED_CONTROL_PREFIX
    if " ".join(text.split()).casefold() != prefix + _TARGETED_CONTROL_SUFFIX:
        return None
    mechanics = TARGETED_CONTROL_SET_MECHANICS
    if per_opponent:
        mechanics = (*mechanics, "fixed-per-player-target-set")
    return ("targeted-creature-control-set-v1", (targeted_control_set_effect(),),
            targeted_control_set_schema(per_opponent=per_opponent), mechanics)


def _fixed_power_control_predicate(subject: str) -> ObjectQuerySpec | None:
    match = re.fullmatch(r"creatures with power (?P<power>[0-9]+) or (?P<bound>greater|less)", subject, re.IGNORECASE)
    if match is None:
        return None
    comparison = TargetNumericComparison.AT_LEAST if match["bound"].casefold() == "greater" else TargetNumericComparison.AT_MOST
    return ObjectQuerySpec(
        zones=("battlefield",), types_all=("creature",),
        numeric_characteristic=TargetNumericCharacteristicSpec(
            TargetNumericCharacteristic.POWER, comparison, int(match["power"]),
        ),
    )


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
    power_query = _fixed_power_control_predicate(subject)
    if power_query is not None:
        query = power_query
    elif subject.casefold() == "permanents you own":
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
    if power_query is not None and steps != ["gain_control", "untap", "haste"]:
        return None
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


def entry_control_exchange_result_template(binding, body):
    if ((binding.event.value, binding.active_zone) != ("permanent.enter", "battlefield")
            or binding.event_condition != entry_exchange_event_condition() or body != binding.body
            or " ".join(body.casefold().split()) != (
                "its controller chooses target permanent another player controls that shares a card type with it. "
                "exchange control of those permanents."
            )):
        return None
    scope = PermanentControlExchange.ENTRY_SHARED_TYPE
    return (
        "entry-shared-type-control-exchange-v1", (scope.to_effect(),), scope.target_schema(),
        (PERMANENT_CONTROL_EXCHANGE_MECHANIC, "cr-115-targets", "cr-611-continuous-effects"),
    )
