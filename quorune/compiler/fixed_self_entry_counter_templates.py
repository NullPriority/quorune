from __future__ import annotations

"""Closed Oracle lowering for mandatory fixed self-entry counters."""

from dataclasses import dataclass
from . import regex_working_set as re
from typing import Any, Mapping

from ..commander_cast_count_model import COMMANDER_CAST_COUNT_CAPABILITY
from ..entry_counter_model import (
    COMMANDER_HISTORY_LOYALTY_ENTRY_CAPABILITY,
    DYNAMIC_SELF_ENTRY_COUNTER_HANDLER_ID,
    INSTANT_SORCERY_LOYALTY_ENTRY_CAPABILITY,
    DynamicEntryCounterAmountSpec,
    DynamicEntryCounterCalculation,
    DynamicEntryCounterValueSource,
    EntryCounterError,
    PAID_COLOR_ENTRY_CAPABILITY,
    PAID_GREEN_PAIRS_ENTRY_CAPABILITY,
    CURRENT_LIFE_ENTRY_COUNTER_CAPABILITY,
    CAST_REVIVAL_ENTRY_CAPABILITY,
    PUBLIC_MAXIMUM_ENTRY_COUNTER_CAPABILITY,
    PUBLIC_MAXIMUM_ENTRY_COUNTER_NAMES,
    ENTRY_CREATURE_VALUE_CAPABILITY,
    ENTRY_CREATURE_VALUE_SOURCES,
    EXILED_MANA_VALUE_ENTRY_COUNTER_CAPABILITY,
)
from ..spell_mana_counts import MANA_SPENT_COUNTS_CAPABILITY
from ..characteristic_fragments import characteristic_quantity_capabilities
from ..keyword_counters import (keyword_counter_mechanic, KeywordCounterError,
    FIXED_KEYWORD_COUNTER_CHOICE_CAPABILITY, KeywordCounterEntryChoiceSpec)
from ..rules.source_references import (
    SourceReferenceSpec,
    source_self_permanent_type,
)
from .counter_placement_templates import FIXED_COUNTER_NAME_PATTERN
from .fixed_numbers import FIXED_COUNT_PATTERN, fixed_number
from .query_characteristic_templates import query_characteristic_quantity, query_characteristic_value


FIXED_SELF_ENTRY_COUNTER_CAPABILITY = "counter.producer.fixed_self_entry"
FIXED_SELF_ENTRY_COUNTER_TEMPLATE = "fixed-self-entry-counter-v1"
DYNAMIC_SELF_ENTRY_COUNTER_CAPABILITY = "counter.producer.dynamic_self_entry"
DYNAMIC_SELF_ENTRY_COUNTER_TEMPLATE = "dynamic-self-entry-counter-v1"
_ENTRY = re.compile(
    rf"^(?P<subject>.+?) enters with "
    rf"(?P<count>an|{FIXED_COUNT_PATTERN}) "
    rf"(?P<counter>{FIXED_COUNTER_NAME_PATTERN}) "
    r"(?P<plural>counter|counters) on (?:it|him|her)\.?$",
    re.IGNORECASE,
)
_DYNAMIC_ENTRY = re.compile(
    r"^(?P<subject>.+?) enters with (?P<body>.+)\.?$",
    re.IGNORECASE,
)
_ESCAPED_ENTRY = re.compile(
    rf"^(?P<subject>.+?) escapes with (?P<count>a|an|{FIXED_COUNT_PATTERN}) "
    rf"(?P<counter>{FIXED_COUNTER_NAME_PATTERN}) "
    r"(?P<plural>counter|counters) on it\.?$",
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
            "counter.placement.quantity_replacement",
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

    compound=re.fullmatch(r'(?P<subject>.+?) enters tapped with (?P<body>.+)',text.strip(),re.I)
    if compound is not None:
        compiled=fixed_self_entry_counter_handler(compound['subject']+' enters with '+compound['body'],source_name=source_name)
        if compiled is None:return None
        return ('fixed-self-tapped-counter-entry-v2',{**compiled[1],
            'handler_id':'replacement.zone.self-tapped-counter-entry.v2','schema_version':2},
            (*compiled[2],'zone.entry.tapped_state'))
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


def fixed_keyword_counter_entry_choice_handler(
    text: str, *, source_name: str,
) -> tuple[str, Mapping[str, Any], tuple[str, ...]] | None:
    """Lower one mandatory choice of a single ordinary keyword counter."""

    match = re.fullmatch(
        r"(?P<subject>.+?) enters with your choice of (?:a|an) "
        r"(?P<first>[a-z ]+) counter or (?:a|an) (?P<second>[a-z ]+) counter on it\.?",
        " ".join(text.strip().split()), re.IGNORECASE,
    )
    if match is None or (
        source_self_permanent_type(match["subject"]) is None
        and not SourceReferenceSpec(source_name).matches(match["subject"])
    ):
        return None
    try:
        spec = KeywordCounterEntryChoiceSpec((match["first"], match["second"]))
    except KeywordCounterError:
        return None
    return (
        "fixed-keyword-counter-entry-choice-v1", spec.descriptor(),
        (FIXED_KEYWORD_COUNTER_CHOICE_CAPABILITY, "counter.placement.quantity_replacement",
         "counter.characteristic.keyword"),
    )


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
    uses_commander_history = amount_spec.value_source is DynamicEntryCounterValueSource.CONTROLLER_COMMANDER_CAST_COUNT
    uses_current_life = amount_spec.value_source is DynamicEntryCounterValueSource.CONTROLLER_LIFE_TOTAL
    uses_public_maximum = amount_spec.value_source in PUBLIC_MAXIMUM_ENTRY_COUNTER_NAMES
    uses_creature_values = amount_spec.value_source in ENTRY_CREATURE_VALUE_SOURCES
    uses_exile = amount_spec.value_source is DynamicEntryCounterValueSource.MAX_EXILED_CARD_MANA_VALUE
    uses_cast_loyalty = amount_spec.value_source is DynamicEntryCounterValueSource.CONTROLLER_INSTANT_SORCERY_CASTS
    uses_cast_revival = amount_spec.value_source is DynamicEntryCounterValueSource.CAST_BY_ENTRY_CONTROLLER
    return (
        DYNAMIC_SELF_ENTRY_COUNTER_TEMPLATE,
        {
            "handler_id": DYNAMIC_SELF_ENTRY_COUNTER_HANDLER_ID,
            "schema_version": 1,
            "event": "zone.change",
            "counter_name": counter_name,
            "amount_spec": amount_spec.to_dict(),
            **({"requires_complete_card_program": True}
               if (amount_spec.mana_color is not None or uses_current_life or uses_public_maximum
                   or uses_creature_values or uses_exile or uses_commander_history or uses_cast_revival) else {}),
            "rule_id": (
                "702.138c"
                if amount_spec.value_source is DynamicEntryCounterValueSource.ESCAPED
                else "614.1c"
            ),
        },
        (
            DYNAMIC_SELF_ENTRY_COUNTER_CAPABILITY,
            *characteristic_quantity_capabilities(amount_spec.quantity),
            *((CURRENT_LIFE_ENTRY_COUNTER_CAPABILITY,) if uses_current_life else ()),
            *((COMMANDER_HISTORY_LOYALTY_ENTRY_CAPABILITY, COMMANDER_CAST_COUNT_CAPABILITY)
              if uses_commander_history else ()),
            *((PUBLIC_MAXIMUM_ENTRY_COUNTER_CAPABILITY,) if uses_public_maximum else ()),
            *((ENTRY_CREATURE_VALUE_CAPABILITY,) if uses_creature_values else ()),
            *((EXILED_MANA_VALUE_ENTRY_COUNTER_CAPABILITY,) if uses_exile else ()),
            *((INSTANT_SORCERY_LOYALTY_ENTRY_CAPABILITY, "condition.history.fixed_public_query")
              if uses_cast_loyalty else ()),
            *((CAST_REVIVAL_ENTRY_CAPABILITY,) if uses_cast_revival else ()),
            *((PAID_COLOR_ENTRY_CAPABILITY,MANA_SPENT_COUNTS_CAPABILITY) if amount_spec.mana_color is not None else ()),
            *((PAID_GREEN_PAIRS_ENTRY_CAPABILITY,)
              if amount_spec.calculation is DynamicEntryCounterCalculation.PAID_GREEN_PAIRS else ()),
            *(
                ("casting.lifecycle.fixed_zone_cast",)
                if amount_spec.value_source is DynamicEntryCounterValueSource.ESCAPED
                else ()
            ),
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
        r"counters on (?:it|him|her)(?:, where X is (?P<definition>.+))?",
        body,
        re.IGNORECASE,
    )
    if x_match is not None:
        definition = x_match.group("definition")
        value_source = DynamicEntryCounterValueSource.CAST_X
        quantity = None
        if definition is not None:
            normalized = " ".join(definition.casefold().split())
            if normalized == "your life total":
                if x_match["counter"].casefold() != "charge" or x_match["twice"]:
                    return None
                value_source = DynamicEntryCounterValueSource.CONTROLLER_LIFE_TOTAL
            elif normalized == "the total life lost by your opponents this turn":
                value_source = DynamicEntryCounterValueSource.OPPONENTS_LIFE_LOST
            elif normalized in {"the amount of life you've gained this turn", "the amount of life you gained this turn"}:
                value_source = DynamicEntryCounterValueSource.CONTROLLER_LIFE_GAINED
            else:
                quantity = query_characteristic_value(
                    definition,
                    source_name=source_name,
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

    normalized_body = " ".join(body.casefold().replace("’", "'").split())
    if re.fullmatch(
        r"a loyalty counter on (?:it|him|her) for each time you've cast a commander from the command zone this game",
        normalized_body,
    ):
        return "loyalty", DynamicEntryCounterAmountSpec(
            value_source=DynamicEntryCounterValueSource.CONTROLLER_COMMANDER_CAST_COUNT,
            calculation=DynamicEntryCounterCalculation.MULTIPLY,
        )

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
        r"on it equal to (?P<plus_one>one plus )?the number of (?P<quantity>.+)",
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
        offset = 1 if numbered is not None and numbered["plus_one"] else 0
        quantity_text = match.group("quantity")
        normalized = " ".join(quantity_text.casefold().split())
        value_source = None
        quantity = None
        if normalized == "color of mana spent to cast it":
            value_source = DynamicEntryCounterValueSource.MANA_COLORS_SPENT
        elif normalized in {"creature that died this turn", "creatures that died this turn"}:
            value_source = DynamicEntryCounterValueSource.CREATURES_DIED
        elif normalized == "creature that died under your control this turn":
            value_source = DynamicEntryCounterValueSource.CONTROLLER_CREATURES_DIED
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
        try:
            amount = DynamicEntryCounterAmountSpec(
                value_source=value_source,
                calculation=DynamicEntryCounterCalculation.MULTIPLY,
                coefficient=coefficient,
                offset=offset,
                quantity=quantity,
            )
        except EntryCounterError:
            return None
        return match.group("counter"), amount

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
        try:
            amount = DynamicEntryCounterAmountSpec(
                value_source=DynamicEntryCounterValueSource.PUBLIC_QUERY,
                calculation=DynamicEntryCounterCalculation.MULTIPLY,
                offset=1,
                quantity=quantity,
            )
        except EntryCounterError:
            return None
        return additive.group("counter"), amount
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
                "a permanent left the battlefield under your control this turn": (
                    DynamicEntryCounterValueSource.CONTROLLER_PERMANENTS_LEFT,
                    1,
                ),
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


_PUBLIC_MAXIMUM_ENTRY_BODIES = {
    "a number of suspect counters on it equal to the greatest number of creatures a player controls":
        DynamicEntryCounterValueSource.MAX_PLAYER_CREATURES,
    "a number of +1/+1 counters on it equal to the greatest number of cards an opponent has drawn this turn":
        DynamicEntryCounterValueSource.MAX_OPPONENT_DRAWS,
}


_ENTRY_CREATURE_VALUE_BODIES = {
    "x +1/+1 counters on it, where x is the greatest power among other creatures you control":
        DynamicEntryCounterValueSource.MAX_OTHER_CONTROLLED_POWER,
    "x +1/+1 counters on it, where x is the greatest power among creatures you control and creature cards in your graveyard":
        DynamicEntryCounterValueSource.MAX_CONTROLLED_POWER_AND_GRAVEYARD,
    "x +1/+1 counters on it, where x is the total toughness of other creatures you control":
        DynamicEntryCounterValueSource.TOTAL_OTHER_CONTROLLED_TOUGHNESS,
    "a +1/+1 counter on it for each +1/+1 counter among other creatures you control":
        DynamicEntryCounterValueSource.TOTAL_OTHER_CONTROLLED_PLUS_COUNTERS,
    "a +1/+1 counter on it for each other red creature you control and a +1/+1 counter on it for each other green creature you control":
        DynamicEntryCounterValueSource.OTHER_CONTROLLED_RED_AND_GREEN_COUNTS,
}


def _dynamic_amount_body(
    body: str,
    *,
    source_name: str,
) -> tuple[str, DynamicEntryCounterAmountSpec] | None:
    if re.fullmatch(
        r"a number of \+1/\+1 counters on it equal to the (?:greatest|highest) mana value among cards in exile",
        body, re.IGNORECASE,
    ):
        return "+1/+1", DynamicEntryCounterAmountSpec(
            value_source=DynamicEntryCounterValueSource.MAX_EXILED_CARD_MANA_VALUE,
            calculation=DynamicEntryCounterCalculation.MULTIPLY,
        )
    if body.casefold().replace("’", "'") == (
        "an additional loyalty counter on him for each instant and sorcery spell you've cast this turn"
    ):
        return "loyalty", DynamicEntryCounterAmountSpec(
            value_source=DynamicEntryCounterValueSource.CONTROLLER_INSTANT_SORCERY_CASTS,
            calculation=DynamicEntryCounterCalculation.MULTIPLY,
        )
    current_creatures = _ENTRY_CREATURE_VALUE_BODIES.get(body.casefold())
    if current_creatures is not None:
        return "+1/+1", DynamicEntryCounterAmountSpec(
            value_source=current_creatures, calculation=DynamicEntryCounterCalculation.MULTIPLY,
        )
    maximum = _PUBLIC_MAXIMUM_ENTRY_BODIES.get(body.casefold())
    if maximum is not None:
        return PUBLIC_MAXIMUM_ENTRY_COUNTER_NAMES[maximum], DynamicEntryCounterAmountSpec(
            value_source=maximum, calculation=DynamicEntryCounterCalculation.MULTIPLY,
        )
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
    if material.casefold() == "this creature enters with eight revival counters on it if you cast it.":
        return _dynamic_compiled(counter_name="revival", amount_spec=DynamicEntryCounterAmountSpec(
            value_source=DynamicEntryCounterValueSource.CAST_BY_ENTRY_CONTROLLER,
            calculation=DynamicEntryCounterCalculation.FIXED_IF_AT_LEAST, coefficient=8, minimum=1,
        ))
    from .paid_color_entry_templates import paid_color_entry_amount
    paid=paid_color_entry_amount(material,source_name=source_name)
    if paid is not None:return _dynamic_compiled(counter_name=paid[0],amount_spec=paid[1])
    escaped = _ESCAPED_ENTRY.fullmatch(material)
    if escaped is not None:
        subject = escaped.group("subject")
        if (
            source_self_permanent_type(subject) is None
            and not SourceReferenceSpec(source_name).matches(subject)
        ):
            return None
        amount = _coefficient(escaped.group("count"), escaped.group("plural"))
        if amount is None:
            return None
        return _dynamic_compiled(
            counter_name=escaped.group("counter"),
            amount_spec=DynamicEntryCounterAmountSpec(
                value_source=DynamicEntryCounterValueSource.ESCAPED,
                calculation=DynamicEntryCounterCalculation.FIXED_IF_AT_LEAST,
                coefficient=amount,
                minimum=1,
            ),
        )
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
    if (amount_spec.value_source is DynamicEntryCounterValueSource.CONTROLLER_LIFE_TOTAL
            and subject.casefold() != "this artifact"):
        return None
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
    "fixed_keyword_counter_entry_choice_handler",
]
