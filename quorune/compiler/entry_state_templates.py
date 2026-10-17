from __future__ import annotations

"""Closed Oracle grammar for battlefield entry tap-state replacements."""

import re
from typing import Any, Mapping

from ..landwalk import BASIC_LAND_TYPES
from ..entry_state_conditions import (
    FIXED_ENTRY_CONDITION_HANDLER_ID,
    FixedEntryCondition,
    FixedEntryMetric,
)
from .fixed_numbers import FIXED_COUNT_PATTERN, fixed_number
from ..rules.source_references import SourceReferenceSpec


EntryStateHandlerTemplate = tuple[str, Mapping[str, Any], str]


_UNCONDITIONAL = re.compile(
    r"^(?P<subject>This (?:artifact|creature|enchantment|land|permanent)) "
    r"enters tapped\.?$",
    re.IGNORECASE,
)
_LIVE_OPPONENTS = re.compile(
    rf"^This land enters tapped unless you have "
    rf"(?P<count>{FIXED_COUNT_PATTERN}|\d+) or more opponents\.?$",
    re.IGNORECASE,
)
_CONTROLLED_BASIC_TYPES = re.compile(
    r"^This land enters (?:the battlefield )?tapped unless you control "
    r"(?P<types>.+)\.?$",
    re.IGNORECASE,
)
_OPTIONAL_LIFE = re.compile(
    rf"^As this land enters, you may pay "
    rf"(?P<amount>{FIXED_COUNT_PATTERN}|\d+) life\. If you don['’]t, "
    rf"it enters tapped\.?$",
    re.IGNORECASE,
)
_AMBIENT_UNTAPPED = re.compile(
    r"^Lands you control enter untapped\.?$",
    re.IGNORECASE,
)
_BASIC_TYPE_LIST = re.compile(
    r"(?:a|an) (?P<kind>Plains|Island|Swamp|Mountain|Forest)",
    re.IGNORECASE,
)
_COUNT = rf"(?P<count>{FIXED_COUNT_PATTERN}|\d+)"
_OTHER_LANDS_UNLESS = re.compile(
    rf"^This land enters tapped unless you control {_COUNT} or "
    rf"(?P<comparison>more|fewer) other lands\.?$",
    re.IGNORECASE,
)
_OTHER_LANDS_IF = re.compile(
    rf"^If you control {_COUNT} or more other lands, "
    rf"this land enters tapped\.?$",
    re.IGNORECASE,
)
_BASIC_LANDS_UNLESS = re.compile(
    rf"^This land enters tapped unless you control {_COUNT} or more "
    rf"basic lands\.?$",
    re.IGNORECASE,
)
_PLAYER_LIFE_UNLESS = re.compile(
    rf"^This land enters tapped unless a player has {_COUNT} or less life\.?$",
    re.IGNORECASE,
)
_OPPONENT_LANDS_UNLESS = re.compile(
    rf"^This land enters tapped unless your opponents control {_COUNT} "
    rf"or more lands\.?$",
    re.IGNORECASE,
)
_BASIC_LAND_UNLESS = re.compile(
    r"^This land enters tapped unless you control a basic land\.?$",
    re.IGNORECASE,
)
_OTHER_BASIC_TYPE_UNLESS = re.compile(
    rf"^This land enters tapped unless you control {_COUNT} or more other "
    rf"(?P<basic_type>Plains|Islands|Swamps|Mountains|Forests)\.?$",
    re.IGNORECASE,
)


def _fixed_condition_descriptor(
    condition: FixedEntryCondition,
) -> dict[str, Any]:
    return {
        "handler_id": FIXED_ENTRY_CONDITION_HANDLER_ID,
        "schema_version": 1,
        "event": "zone.change",
        "subject": {"types_all": ["land"]},
        "condition": condition.to_dict(),
    }


def _minimum_condition(
    metric: FixedEntryMetric,
    count: int,
    *,
    tapped_when_met: bool = False,
) -> FixedEntryCondition | None:
    return (
        FixedEntryCondition(metric, count, None, tapped_when_met)
        if count > 0
        else None
    )


def _maximum_condition(
    metric: FixedEntryMetric,
    count: int,
) -> FixedEntryCondition:
    return FixedEntryCondition(metric, None, count, False)


def _fixed_entry_condition(
    text: str,
    *,
    source_name: str,
) -> tuple[str, FixedEntryCondition] | None:
    other_lands = _OTHER_LANDS_UNLESS.fullmatch(text)
    if other_lands is not None:
        count = _count(other_lands.group("count"))
        condition = (
            _minimum_condition(FixedEntryMetric.CONTROLLER_LANDS, count)
            if other_lands.group("comparison").casefold() == "more"
            else _maximum_condition(FixedEntryMetric.CONTROLLER_LANDS, count)
        )
        if condition is None:
            return None
        return (
            "zone-entry-state-self-other-land-count-v1",
            condition,
        )

    tapped_if = _OTHER_LANDS_IF.fullmatch(text)
    if tapped_if is not None:
        count = _count(tapped_if.group("count"))
        condition = _minimum_condition(
            FixedEntryMetric.CONTROLLER_LANDS,
            count,
            tapped_when_met=True,
        )
        return (
            ("zone-entry-state-self-other-land-count-tapped-when-met-v1", condition)
            if condition is not None
            else None
        )

    basic_lands = _BASIC_LANDS_UNLESS.fullmatch(text)
    if basic_lands is not None:
        condition = _minimum_condition(
            FixedEntryMetric.CONTROLLER_BASIC_LANDS,
            _count(basic_lands.group("count")),
        )
        return (
            ("zone-entry-state-self-basic-land-count-v1", condition)
            if condition is not None
            else None
        )

    player_life = _PLAYER_LIFE_UNLESS.fullmatch(text)
    if player_life is not None:
        return (
            "zone-entry-state-self-player-life-maximum-v1",
            _maximum_condition(
                FixedEntryMetric.MINIMUM_PLAYER_LIFE,
                _count(player_life.group("count")),
            ),
        )

    opponent_lands = _OPPONENT_LANDS_UNLESS.fullmatch(text)
    if opponent_lands is not None:
        condition = _minimum_condition(
            FixedEntryMetric.OPPONENT_LANDS,
            _count(opponent_lands.group("count")),
        )
        return (
            ("zone-entry-state-self-opponent-land-count-v1", condition)
            if condition is not None
            else None
        )

    if _BASIC_LAND_UNLESS.fullmatch(text) is not None:
        return (
            "zone-entry-state-self-controlled-basic-land-v1",
            FixedEntryCondition(
                FixedEntryMetric.CONTROLLER_BASIC_LANDS,
                1,
                None,
                False,
            ),
        )

    other_basic = _OTHER_BASIC_TYPE_UNLESS.fullmatch(text)
    if other_basic is not None:
        metric = {
            "plains": FixedEntryMetric.CONTROLLER_PLAINS,
            "islands": FixedEntryMetric.CONTROLLER_ISLANDS,
            "swamps": FixedEntryMetric.CONTROLLER_SWAMPS,
            "mountains": FixedEntryMetric.CONTROLLER_MOUNTAINS,
            "forests": FixedEntryMetric.CONTROLLER_FORESTS,
        }[other_basic.group("basic_type").casefold()]
        condition = _minimum_condition(
            metric,
            _count(other_basic.group("count")),
        )
        return (
            ("zone-entry-state-self-controlled-basic-subtype-count-v1", condition)
            if condition is not None
            else None
        )

    source = SourceReferenceSpec(source_name).regex_pattern if source_name else None
    subject = rf"(?:This land|{source})" if source else r"This land"
    closed_queries = (
        (
            rf"^{subject} enters tapped unless you control a legendary creature\.?$",
            "zone-entry-state-self-controlled-legendary-creature-v1",
            FixedEntryMetric.CONTROLLER_LEGENDARY_CREATURES,
        ),
        (
            rf"^{subject} enters tapped unless you control a legendary green creature\.?$",
            "zone-entry-state-self-controlled-legendary-green-creature-v1",
            FixedEntryMetric.CONTROLLER_LEGENDARY_GREEN_CREATURES,
        ),
        (
            rf"^{subject} enters tapped unless you control a Mount or Vehicle\.?$",
            "zone-entry-state-self-controlled-mount-or-vehicle-v1",
            FixedEntryMetric.CONTROLLER_MOUNTS_OR_VEHICLES,
        ),
    )
    for pattern, template_id, metric in closed_queries:
        if re.fullmatch(pattern, text, re.IGNORECASE) is not None:
            return (
                template_id,
                FixedEntryCondition(metric, 1, None, False),
            )
    return None


def _count(value: str) -> int:
    return int(value) if value.isdigit() else fixed_number(value)


def _descriptor(
    *,
    source_relation: str,
    subject_types: tuple[str, ...],
    tapped: bool,
    minimum_opponents: int | None = None,
    controlled_basic_types_any: tuple[str, ...] = (),
    optional_life: int = 0,
) -> dict[str, Any]:
    return {
        "handler_id": "replacement.zone.entry-state.v1",
        "schema_version": 1,
        "event": "zone.change",
        "source_relation": source_relation,
        "subject": {"types_all": list(subject_types)},
        "condition": {
            "minimum_opponents": minimum_opponents,
            "controlled_basic_types_any": list(
                controlled_basic_types_any
            ),
        },
        "instruction": {
            "tapped": tapped,
            "optional_life": optional_life,
        },
    }


def _unconditional_match(text: str, source_name: str) -> bool:
    if _UNCONDITIONAL.fullmatch(text) is not None:
        return True
    if not source_name:
        return False
    source = SourceReferenceSpec(source_name).regex_pattern
    return (
        re.fullmatch(
            rf"{source} enters tapped\.?",
            text,
            re.IGNORECASE,
        )
        is not None
    )


def _controlled_basic_types(text: str) -> tuple[str, ...] | None:
    match = _CONTROLLED_BASIC_TYPES.fullmatch(text)
    if match is None:
        return None
    clause = match.group("types")
    matches = tuple(
        value.casefold()
        for value in _BASIC_TYPE_LIST.findall(clause)
    )
    if not matches or len(matches) != len(set(matches)):
        return None
    reconstructed = " or ".join(
        f"{'an' if value[0] in 'aeiou' else 'a'} {value.title()}"
        for value in matches
    )
    if clause.rstrip(".").casefold() != reconstructed.casefold():
        return None
    if any(value not in BASIC_LAND_TYPES for value in matches):
        return None
    return tuple(sorted(matches))


def static_entry_state_handler(
    text: str,
    *,
    source_name: str,
) -> EntryStateHandlerTemplate | None:
    """Lower one exact tap-state replacement from a closed grammar."""

    from .external_entry_state_templates import external_entry_state_handler
    external=external_entry_state_handler(text)
    if external is not None:return external

    normalized = text.strip()
    if _unconditional_match(normalized, source_name):
        return (
            "zone-entry-state-self-tapped-v1",
            _descriptor(
                source_relation="affected_object",
                subject_types=(),
                tapped=True,
            ),
            "zone.entry.tapped_state",
        )

    opponents = _LIVE_OPPONENTS.fullmatch(normalized)
    if opponents is not None:
        count = _count(opponents.group("count"))
        if count < 1:
            return None
        return (
            f"zone-entry-state-self-minimum-{count}-opponents-v1",
            _descriptor(
                source_relation="affected_object",
                subject_types=("land",),
                tapped=True,
                minimum_opponents=count,
            ),
            "zone.entry.tapped_state",
        )

    controlled_types = _controlled_basic_types(normalized)
    if controlled_types is not None:
        return (
            "zone-entry-state-self-controlled-basic-types-v1",
            _descriptor(
                source_relation="affected_object",
                subject_types=("land",),
                tapped=True,
                controlled_basic_types_any=controlled_types,
            ),
            "zone.entry.tapped_state",
        )

    fixed_condition = _fixed_entry_condition(
        normalized,
        source_name=source_name,
    )
    if fixed_condition is not None:
        template_id, condition = fixed_condition
        return (
            template_id,
            _fixed_condition_descriptor(condition),
            "zone.entry.tapped_state.fixed_condition",
        )

    optional_life = _OPTIONAL_LIFE.fullmatch(normalized)
    if optional_life is not None:
        amount = _count(optional_life.group("amount"))
        if amount < 1:
            return None
        return (
            f"zone-entry-state-self-pay-{amount}-life-v1",
            _descriptor(
                source_relation="affected_object",
                subject_types=("land",),
                tapped=True,
                optional_life=amount,
            ),
            "zone.entry.tapped_state",
        )

    if _AMBIENT_UNTAPPED.fullmatch(normalized) is not None:
        return (
            "zone-entry-state-controlled-lands-untapped-v1",
            _descriptor(
                source_relation="controller",
                subject_types=("land",),
                tapped=False,
            ),
            "zone.entry.tapped_state",
        )
    return None


__all__ = ["EntryStateHandlerTemplate", "static_entry_state_handler"]
