from __future__ import annotations

"""Closed Oracle lowering for fixed named counter-removal effects."""

from dataclasses import dataclass
import re
from typing import Any, Mapping

from .direct_target import (
    DirectPermanentTargetSpec,
    direct_permanent_target_spec,
)
from .fixed_numbers import fixed_number
from ..rules.source_references import SourceReferenceSpec


_COUNT = r"a|an|one|two|three|four|five|six|seven|eight|nine|ten|\d+"
_COUNTER_NAME = (
    r"[+-]\d+/[+-]\d+|"
    r"[A-Za-z][A-Za-z'-]*(?: [A-Za-z][A-Za-z'-]*){0,2}"
)
_REMOVAL = re.compile(
    rf"remove (?P<count>{_COUNT}) (?P<counter>{_COUNTER_NAME}) "
    r"(?P<plural>counter|counters) from (?P<subject>.+?)\.?",
    re.IGNORECASE,
)
_ALL_REMOVAL = re.compile(
    r"remove all counters from (?P<subject>.+?)\.?",
    re.IGNORECASE,
)
_ALL_NAMED_REMOVAL = re.compile(
    rf'remove all (?P<counter>{_COUNTER_NAME}) counters from (?P<subject>.+?)\.?',
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class FixedCounterRemovalTemplate:
    """One mandatory fixed named-counter removal from one direct target."""

    count: int
    counter_name: str
    target_spec: DirectPermanentTargetSpec

    def __post_init__(self) -> None:
        if type(self.count) is not int or self.count <= 0:
            raise ValueError("Counter removal count must be positive")
        if type(self.counter_name) is not str or not self.counter_name:
            raise ValueError("Counter removal name must be nonempty")
        if not isinstance(self.target_spec, DirectPermanentTargetSpec):
            raise ValueError("Counter removal requires one typed target")

    @property
    def template_id(self) -> str:
        version = 2 if self.target_spec.uses_compound_characteristics else 1
        return f"remove-fixed-counter-{self.target_spec.slug}-v{version}"

    @property
    def effects(self) -> tuple[Mapping[str, Any], ...]:
        return (
            {
                "op": "remove_counters",
                "card": "$target.0",
                "counter": self.counter_name,
                "amount": self.count,
                "source": "$source",
            },
        )

    @property
    def target_schema(self) -> Mapping[str, Any]:
        return self.target_spec.to_target_schema()

    @property
    def mechanics(self) -> tuple[str, ...]:
        return ("cr-122-counters", "cr-115-targets")

    def compiled(
        self,
    ) -> tuple[
        str,
        tuple[Mapping[str, Any], ...],
        Mapping[str, Any],
        tuple[str, ...],
    ]:
        return (
            self.template_id,
            self.effects,
            self.target_schema,
            self.mechanics,
        )


@dataclass(frozen=True, slots=True)
class AllCounterRemovalTemplate:
    """Every counter kind on one direct permanent target."""

    target_spec: DirectPermanentTargetSpec

    def __post_init__(self) -> None:
        if not isinstance(self.target_spec, DirectPermanentTargetSpec):
            raise ValueError("All-counter removal requires one typed target")

    @property
    def template_id(self) -> str:
        version = 2 if self.target_spec.uses_compound_characteristics else 1
        return f"remove-all-counters-{self.target_spec.slug}-v{version}"

    @property
    def effects(self) -> tuple[Mapping[str, Any], ...]:
        return (
            {
                "op": "remove_all_counters",
                "card": "$target.0",
                "source": "$source",
            },
        )

    @property
    def target_schema(self) -> Mapping[str, Any]:
        return self.target_spec.to_target_schema()

    @property
    def mechanics(self) -> tuple[str, ...]:
        return ("cr-122-counters", "cr-115-targets")

    def compiled(
        self,
    ) -> tuple[
        str,
        tuple[Mapping[str, Any], ...],
        Mapping[str, Any],
        tuple[str, ...],
    ]:
        return (
            self.template_id,
            self.effects,
            self.target_schema,
            self.mechanics,
        )


def fixed_counter_removal_effect_template(
    text: str,
) -> FixedCounterRemovalTemplate | None:
    """Parse only one closed fixed named-counter removal clause."""

    if type(text) is not str:
        return None
    match = _REMOVAL.fullmatch(text.strip())
    if match is None:
        return None
    count = fixed_number(match.group("count"))
    if count <= 0 or (match.group("plural").casefold() == "counter") != (
        count == 1
    ):
        return None
    target = direct_permanent_target_spec(match.group("subject"))
    if target is None:
        return None
    return FixedCounterRemovalTemplate(
        count=count,
        counter_name=" ".join(match.group("counter").casefold().split()),
        target_spec=target,
    )


def all_counter_removal_effect_template(
    text: str,
) -> AllCounterRemovalTemplate | None:
    """Parse only mandatory all-counter removal from one direct target."""

    if type(text) is not str:
        return None
    match = _ALL_REMOVAL.fullmatch(text.strip())
    if match is None:
        return None
    target = direct_permanent_target_spec(match.group("subject"))
    if target is None:
        return None
    return AllCounterRemovalTemplate(target_spec=target)


def fixed_source_counter_removal_effect_template(
    text: str, *, card_name: str, allow_source_pronoun: bool = False,
):
    """Bind one mandatory removal to the same battlefield source incarnation."""
    if type(text) is not str:
        return None
    match = _REMOVAL.fullmatch(text.strip())
    if match is None:
        return None
    count = fixed_number(match['count'])
    if count <= 0 or (match['plural'].casefold() == 'counter') != (count == 1):
        return None
    subject = match['subject']
    if not (re.fullmatch(r'this (?:creature|artifact|enchantment|land|permanent|planeswalker|Equipment|Vehicle)', subject, re.I)
            or SourceReferenceSpec(card_name).matches(subject)
            or allow_source_pronoun and subject.casefold() in {'it', 'him', 'her'}):
        return None
    return ('remove-fixed-counter-source-incarnation-v1', ({
        'op': 'remove_counters', 'card': '$source.zone_object',
        'counter': ' '.join(match['counter'].casefold().split()),
        'amount': count, 'source': '$source',
    },), None, ('cr-122-counters',))


def all_named_source_counter_removal_effect_template(
    text: str, *, card_name: str, allow_source_pronoun: bool = False,
):
    """Select one named kind, without a numeric amount or a target."""
    match = _ALL_NAMED_REMOVAL.fullmatch(text.strip()) if type(text) is str else None
    if match is None:
        return None
    subject = match['subject']
    if not (re.fullmatch(r'this (?:creature|artifact|enchantment|land|permanent|planeswalker|Equipment|Vehicle)', subject, re.I)
            or SourceReferenceSpec(card_name).matches(subject)
            or allow_source_pronoun and subject.casefold() in {'it', 'him', 'her'}):
        return None
    return ('remove-all-named-counter-source-incarnation-v2', ({
        'op': 'remove_all_counters', 'schema_version': 2,
        'card': '$source.zone_object', 'counter': ' '.join(match['counter'].casefold().split()),
        'source': '$source',
    },), None, ('cr-122-counters',))


__all__ = [
    "all_counter_removal_effect_template",
    "AllCounterRemovalTemplate",
    "FixedCounterRemovalTemplate",
    "fixed_counter_removal_effect_template",
    "fixed_source_counter_removal_effect_template",
    "all_named_source_counter_removal_effect_template",
]
