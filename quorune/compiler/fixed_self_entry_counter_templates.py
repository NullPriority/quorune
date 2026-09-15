from __future__ import annotations

"""Closed Oracle lowering for mandatory fixed self-entry counters."""

from dataclasses import dataclass
import re
from typing import Any, Mapping

from ..entry_counter_model import (
    DYNAMIC_SELF_ENTRY_COUNTER_HANDLER_ID,
    DynamicEntryCounterAmountSpec,
    DynamicEntryCounterCalculation,
    DynamicEntryCounterValueSource,
)
from ..keyword_counters import keyword_counter_mechanic
from ..rules.source_references import (
    SourceReferenceSpec,
    source_self_permanent_type,
)
from .counter_placement_templates import FIXED_COUNTER_NAME_PATTERN
from .fixed_numbers import FIXED_COUNT_PATTERN, fixed_number
from .query_characteristic_templates import query_characteristic_quantity


FIXED_SELF_ENTRY_COUNTER_CAPABILITY = "counter.producer.fixed_self_entry"
FIXED_SELF_ENTRY_COUNTER_TEMPLATE = "fixed-self-entry-counter-v1"
DYNAMIC_SELF_ENTRY_COUNTER_CAPABILITY = "counter.producer.dynamic_self_entry"
DYNAMIC_SELF_ENTRY_COUNTER_TEMPLATE = "dynamic-self-entry-counter-v1"
_ENTRY = re.compile(
    rf"^(?P<subject>.+?) enters with "
    rf"(?P<count>an|{FIXED_COUNT_PATTERN}) "
    rf"(?P<counter>{FIXED_COUNTER_NAME_PATTERN}) "
    r"(?P<plural>counter|counters) on it\.?$",
    re.IGNORECASE,
)
_DYNAMIC_ENTRY = re.compile(
    r"^(?P<subject>.+?) enters with (?P<body>.+)\.?$",
    re.IGNORECASE,
)
_ABILITY_WORD = re.compile(
    r"^[A-Z][A-Za-z0-9' ]{0,80}\s+(?:—|�)\s+(?P<body>.+)$"
)


@dataclass(frozen=True, slots=True)
class FixedSelfEntryCounterTemplate:
    counter_name: str
    amount: int

    def __post_init__(self) -> None:
        name = " ".join(str(self.counter_name).casefold().split())
        if not name:
            raise ValueError("Self-entry counter name must be nonempty")
        if type(self.amount) is not int or not 1 <= self.amount <= 10:
            raise ValueError("Self-entry counter amount must be from 1 through 10")
        object.__setattr__(self, "counter_name", name)

    @property
    def capabilities(self) -> tuple[str, ...]:
        return (
            FIXED_SELF_ENTRY_COUNTER_CAPABILITY,
            *(
                ("counter.characteristic.keyword",)
                if keyword_counter_mechanic(self.counter_name) is not None
                else ()
            ),
        )

    def compiled(
        self,
    ) -> tuple[str, Mapping[str, Any], tuple[str, ...]]:
        return (
            FIXED_SELF_ENTRY_COUNTER_TEMPLATE,
            {
                "handler_id": "replacement.zone.self-entry-counter.v1",
                "schema_version": 1,
                "event": "zone.change",
                "counter_name": self.counter_name,
                "amount": self.amount,
                "optional": False,
                "rule_id": "614.1c",
            },
            self.capabilities,
        )


def fixed_self_entry_counter_handler(
    text: str,
    *,
    source_name: str,
) -> tuple[str, Mapping[str, Any], tuple[str, ...]] | None:
    """Lower one source-relative mandatory fixed entry-counter sentence."""

    match = _ENTRY.fullmatch(" ".join(text.strip().split()))
    if match is None:
        return None
    subject = match.group("subject")
    if (
        source_self_permanent_type(subject) is None
        and not SourceReferenceSpec(source_name).matches(subject)
    ):
        return None
    amount = fixed_number(match.group("count"))
    if (
        not 1 <= amount <= 10
        or (match.group("plural").casefold() == "counter") != (amount == 1)
    ):
        return None
    return FixedSelfEntryCounterTemplate(
        counter_name=match.group("counter"),
        amount=amount,
    ).compiled()


def _coefficient(value: str, plural: str) -> int | None:
    amount = 1 if value.casefold() in {"a", "an"} else fixed_number(value)
    if (
        not 1 <= amount <= 10
        or (plural.casefold() == "counter") != (amount == 1)
    ):
        return None
    return amount


def _dynamic_compiled(
    *,
    counter_name: str,
    amount_spec: DynamicEntryCounterAmountSpec,
) -> tuple[str, Mapping[str, Any], tuple[str, ...]]:
    return (
        DYNAMIC_SELF_ENTRY_COUNTER_TEMPLATE,
        {
            "handler_id": DYNAMIC_SELF_ENTRY_COUNTER_HANDLER_ID,
            "schema_version": 1,
            "event": "zone.change",
            "counter_name": counter_name,
            "amount_spec": amount_spec.to_dict(),
            "rule_id": "614.1c",
        },
        (
            DYNAMIC_SELF_ENTRY_COUNTER_CAPABILITY,
            *(
                ("counter.characteristic.keyword",)
                if keyword_counter_mechanic(counter_name) is not None
                else ()
            ),
        ),
    )


def _x_dynamic_amount_body(
    body: str,
    *,
    source_name: str,
) -> tuple[str, DynamicEntryCounterAmountSpec] | None:
    x_match = re.fullmatch(
        rf"(?P<twice>twice )?X (?P<counter>{FIXED_COUNTER_NAME_PATTERN}) "
        r"counters on it(?:, where X is (?P<definition>.+))?",
        body,
        re.IGNORECASE,
    )
    if x_match is not None:
        definition = x_match.group("definition")
        value_source = DynamicEntryCounterValueSource.CAST_X
        quantity = None
        if definition is not None:
            normalized = " ".join(definition.casefold().split())
            if normalized == "the total life lost by your opponents this turn":
                value_source = DynamicEntryCounterValueSource.OPPONENTS_LIFE_LOST
            else:
                query_match = re.fullmatch(
                    r"the number of (?P<quantity>.+)",
                    definition,
                    re.IGNORECASE,
                )
                if query_match is None:
                    return None
                quantity = query_characteristic_quantity(
                    query_match.group("quantity"),
                    source_name=source_name,
                    definition_extensions=True,
                )
                if quantity is None:
                    return None
                value_source = DynamicEntryCounterValueSource.PUBLIC_QUERY
        return (
            x_match.group("counter"),
            DynamicEntryCounterAmountSpec(
                value_source=value_source,
                calculation=DynamicEntryCounterCalculation.MULTIPLY,
                coefficient=2 if x_match.group("twice") else 1,
                quantity=quantity,
            ),
        )
    return None


def _counted_dynamic_amount_body(
    body: str,
    *,
    source_name: str,
) -> tuple[str, DynamicEntryCounterAmountSpec] | None:

    counted = re.fullmatch(
        rf"(?P<count>a|an|{FIXED_COUNT_PATTERN}) "
        rf"(?P<counter>{FIXED_COUNTER_NAME_PATTERN}) "
        r"(?P<plural>counter|counters) on it "
        r"(?P<relation>for each|equal to the number of) (?P<quantity>.+)",
        body,
        re.IGNORECASE,
    )
    numbered = re.fullmatch(
        rf"a number of (?P<counter>{FIXED_COUNTER_NAME_PATTERN}) counters "
        r"on it equal to the number of (?P<quantity>.+)",
        body,
        re.IGNORECASE,
    )
    if counted is not None or numbered is not None:
        match = counted or numbered
        assert match is not None
        coefficient = (
            _coefficient(counted.group("count"), counted.group("plural"))
            if counted is not None
            else 1
        )
        if coefficient is None:
            return None
        quantity_text = match.group("quantity")
        normalized = " ".join(quantity_text.casefold().split())
        value_source = None
        quantity = None
        if normalized == "color of mana spent to cast it":
            value_source = DynamicEntryCounterValueSource.MANA_COLORS_SPENT
        elif normalized == "creature that died this turn":
            value_source = DynamicEntryCounterValueSource.CREATURES_DIED
        elif normalized == "other spell cast this turn":
            value_source = DynamicEntryCounterValueSource.OTHER_SPELLS_CAST
        else:
            quantity = query_characteristic_quantity(
                quantity_text,
                source_name=source_name,
                definition_extensions=True,
            )
            if quantity is None:
                return None
            value_source = DynamicEntryCounterValueSource.PUBLIC_QUERY
        return (
            match.group("counter"),
            DynamicEntryCounterAmountSpec(
                value_source=value_source,
                calculation=DynamicEntryCounterCalculation.MULTIPLY,
                coefficient=coefficient,
                quantity=quantity,
            ),
        )

    additive = re.fullmatch(
        rf"a (?P<counter>{FIXED_COUNTER_NAME_PATTERN}) counter on it plus "
        rf"an additional (?P=counter) counter on it for each (?P<quantity>.+)",
        body,
        re.IGNORECASE,
    )
    if additive is not None:
        quantity = query_characteristic_quantity(
            additive.group("quantity"),
            source_name=source_name,
            definition_extensions=True,
        )
        if quantity is None:
            return None
        return (
            additive.group("counter"),
            DynamicEntryCounterAmountSpec(
                value_source=DynamicEntryCounterValueSource.PUBLIC_QUERY,
                calculation=DynamicEntryCounterCalculation.MULTIPLY,
                offset=1,
                quantity=quantity,
            ),
        )
    return None


def _conditional_dynamic_amount_body(
    body: str,
    *,
    source_name: str,
) -> tuple[str, DynamicEntryCounterAmountSpec] | None:

    conditional = re.fullmatch(
        rf"(?P<count>a|an|{FIXED_COUNT_PATTERN}) "
        rf"(?P<counter>{FIXED_COUNTER_NAME_PATTERN}) "
        r"(?P<plural>counter|counters) on it if (?P<condition>.+)",
        body,
        re.IGNORECASE,
    )
    unless_colors = re.fullmatch(
        rf"(?P<count>a|an|{FIXED_COUNT_PATTERN}) "
        rf"(?P<counter>{FIXED_COUNTER_NAME_PATTERN}) "
        r"(?P<plural>counter|counters) on it unless two or more colors of "
        r"mana were spent to cast it",
        body,
        re.IGNORECASE,
    )
    if conditional is not None or unless_colors is not None:
        match = conditional or unless_colors
        assert match is not None
        amount = _coefficient(match.group("count"), match.group("plural"))
        if amount is None:
            return None
        if unless_colors is not None:
            source = DynamicEntryCounterValueSource.MANA_COLORS_SPENT
            minimum = 2
            calculation = DynamicEntryCounterCalculation.FIXED_IF_BELOW
        else:
            condition = " ".join(
                conditional.group("condition")
                .casefold()
                .replace("’", "'")
                .split()
            )
            conditions = {
                "you attacked this turn": (
                    DynamicEntryCounterValueSource.CONTROLLER_ATTACKED,
                    1,
                ),
                "an opponent lost life this turn": (
                    DynamicEntryCounterValueSource.OPPONENTS_LIFE_LOST,
                    1,
                ),
                "a creature died this turn": (
                    DynamicEntryCounterValueSource.CREATURES_DIED,
                    1,
                ),
                "one or more creatures died this turn": (
                    DynamicEntryCounterValueSource.CREATURES_DIED,
                    1,
                ),
                "you've cast another spell this turn": (
                    DynamicEntryCounterValueSource.CONTROLLER_OTHER_SPELLS_CAST,
                    1,
                ),
                "you've cast two or more spells this turn": (
                    DynamicEntryCounterValueSource.CONTROLLER_SPELLS_CAST,
                    2,
                ),
                "you cast it from your hand": (
                    DynamicEntryCounterValueSource.CAST_FROM_HAND,
                    1,
                ),
                "it wasn't cast or no mana was spent to cast it": (
                    DynamicEntryCounterValueSource.MANA_WAS_SPENT,
                    1,
                ),
            }
            if condition not in conditions:
                return None
            source, minimum = conditions[condition]
            calculation = (
                DynamicEntryCounterCalculation.FIXED_IF_BELOW
                if source is DynamicEntryCounterValueSource.MANA_WAS_SPENT
                else DynamicEntryCounterCalculation.FIXED_IF_AT_LEAST
            )
        return (
            match.group("counter"),
            DynamicEntryCounterAmountSpec(
                value_source=source,
                calculation=calculation,
                coefficient=amount,
                minimum=minimum,
            ),
        )
    return None


def _dynamic_amount_body(
    body: str,
    *,
    source_name: str,
) -> tuple[str, DynamicEntryCounterAmountSpec] | None:
    for compiler in (
        _x_dynamic_amount_body,
        _counted_dynamic_amount_body,
        _conditional_dynamic_amount_body,
    ):
        compiled = compiler(body, source_name=source_name)
        if compiled is not None:
            return compiled
    return None


def dynamic_self_entry_counter_handler(
    text: str,
    *,
    source_name: str,
) -> tuple[str, Mapping[str, Any], tuple[str, ...]] | None:
    """Lower one dynamic self-entry counter through frozen public facts."""

    material = " ".join(text.strip().split())
    ability_word = _ABILITY_WORD.fullmatch(material)
    if ability_word is not None:
        material = ability_word.group("body")
    match = _DYNAMIC_ENTRY.fullmatch(material)
    if match is None:
        return None
    subject = match.group("subject")
    if (
        source_self_permanent_type(subject) is None
        and not SourceReferenceSpec(source_name).matches(subject)
    ):
        return None
    compiled = _dynamic_amount_body(
        match.group("body").removesuffix("."),
        source_name=source_name,
    )
    if compiled is None:
        return None
    counter_name, amount_spec = compiled
    return _dynamic_compiled(
        counter_name=counter_name,
        amount_spec=amount_spec,
    )


__all__ = [
    "DYNAMIC_SELF_ENTRY_COUNTER_CAPABILITY",
    "DYNAMIC_SELF_ENTRY_COUNTER_TEMPLATE",
    "dynamic_self_entry_counter_handler",
    "FIXED_SELF_ENTRY_COUNTER_CAPABILITY",
    "FIXED_SELF_ENTRY_COUNTER_TEMPLATE",
    "FixedSelfEntryCounterTemplate",
    "fixed_self_entry_counter_handler",
]
