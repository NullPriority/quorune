from __future__ import annotations

"""Closed lowering for fixed counter placements on several subjects."""

from dataclasses import dataclass, replace
import hashlib
from . import regex_working_set as re
from typing import Any, Mapping

from ..keyword_counters import keyword_counter_mechanic
from ..util import stable_json
from .counter_placement_templates import (
    CounterPlacementSubject,
    FixedCounterPlacementTemplate,
    fixed_counter_placement_effect_template,
)


_CLAUSE_SEPARATOR = re.compile(
    r"\s*,\s*(?:and\s+)?|\s+and\s+",
    re.IGNORECASE,
)
_TARGET_PREFIX = re.compile(
    r"\bon (?P<optional>up to one )?"
    r"(?P<ordinal>another |a third |other )?target\b",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class FixedCounterPlacementGroupTemplate:
    """One same-kind fixed placement instruction spanning two or three subjects."""

    placements: tuple[FixedCounterPlacementTemplate, ...]
    optional_targets: tuple[bool, ...]
    globally_distinct: bool = False

    def __post_init__(self) -> None:
        placements = tuple(self.placements)
        optional_targets = tuple(self.optional_targets)
        if not 2 <= len(placements) <= 3:
            raise ValueError("Counter placement groups require two or three subjects")
        if len(optional_targets) != len(placements) or any(
            type(value) is not bool for value in optional_targets
        ):
            raise ValueError(
                "Counter placement groups require one optional marker per subject"
            )
        if any(
            placement.subject
            not in {CounterPlacementSubject.SOURCE, CounterPlacementSubject.TARGET}
            for placement in placements
        ):
            raise ValueError(
                "Counter placement groups support only source and direct targets"
            )
        if len(
            {
                (placement.counter_name, placement.count)
                for placement in placements
            }
        ) != 1:
            raise ValueError(
                "Counter placement groups require one fixed counter kind and amount"
            )
        source_count = sum(
            placement.subject is CounterPlacementSubject.SOURCE
            for placement in placements
        )
        target_count = len(placements) - source_count
        if source_count > 1 or target_count < 1:
            raise ValueError(
                "Counter placement groups require at most one source and at least one target"
            )
        for index, (placement, optional) in enumerate(
            zip(placements, optional_targets, strict=True)
        ):
            if optional and (
                placement.subject is not CounterPlacementSubject.TARGET
                or index != len(placements) - 1
            ):
                raise ValueError(
                    "Only the final direct target in a counter group may be optional"
                )
        if type(self.globally_distinct) is not bool or (
            self.globally_distinct and target_count < 2
        ):
            raise ValueError(
                "Global target distinctness requires at least two target subjects"
            )
        object.__setattr__(self, "placements", placements)
        object.__setattr__(self, "optional_targets", optional_targets)

    @property
    def template_id(self) -> str:
        fingerprint = hashlib.sha256(
            stable_json(
                {
                    "effects": self.effects,
                    "target_schema": self.target_schema,
                }
            ).encode("utf-8")
        ).hexdigest()
        return f"place-fixed-counter-group-{fingerprint[:16]}-v1"

    @property
    def effects(self) -> tuple[Mapping[str, Any], ...]:
        first = self.placements[0]
        cards: list[str] = []
        target_index = 0
        for placement in self.placements:
            if placement.subject is CounterPlacementSubject.SOURCE:
                cards.append("$source.zone_object")
                continue
            cards.append(f"$target.{target_index}")
            target_index += 1
        return (
            {
                "op": "place_counters",
                "cards": cards,
                "counter": first.counter_name,
                "amount": first.count,
                "source": "$source",
            },
        )

    @property
    def target_schema(self) -> Mapping[str, Any]:
        groups: list[dict[str, Any]] = []
        target_index = 0
        for placement, optional in zip(
            self.placements,
            self.optional_targets,
            strict=True,
        ):
            if placement.subject is not CounterPlacementSubject.TARGET:
                continue
            schema = dict(placement.target_schema or {})
            schema["id"] = f"target_{target_index}"
            schema.pop("count", None)
            if optional:
                schema["min"] = 0
                schema["max"] = 1
            else:
                schema["count"] = 1
            groups.append(schema)
            target_index += 1
        result: dict[str, Any] = {"groups": groups}
        if self.globally_distinct:
            result["globally_distinct"] = True
        return result

    @property
    def mechanics(self) -> tuple[str, ...]:
        counter_mechanic = keyword_counter_mechanic(
            self.placements[0].counter_name
        )
        return (
            "cr-122-counters",
            "cr-115-targets",
            *((counter_mechanic,) if counter_mechanic is not None else ()),
        )

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


def _normalized_group_clause(
    raw_clause: str,
    *,
    first: bool,
) -> tuple[str, bool, str | None, bool] | None:
    clause = raw_clause.strip()
    if not clause:
        return None
    starts_with_put = re.match(r"put\s+", clause, re.IGNORECASE) is not None
    if first and not starts_with_put:
        return None
    if not first and not starts_with_put:
        clause = f"Put {clause}"
    target_matches = tuple(_TARGET_PREFIX.finditer(clause))
    if len(target_matches) > 1:
        return None
    optional = False
    ordinal: str | None = None
    commander = False
    if target_matches:
        match = target_matches[0]
        optional = match.group("optional") is not None
        ordinal = (
            str(match.group("ordinal") or "").strip().casefold() or None
        )
        remainder = clause[match.end() :]
        commander_match = re.match(
            r" commander creature\b",
            remainder,
            re.IGNORECASE,
        )
        if commander_match is not None:
            commander = True
            remainder = (
                " creature" + remainder[commander_match.end() :]
            )
        clause = (
            clause[: match.start()]
            + "on target"
            + remainder
        )
    return clause, optional, ordinal, commander


def fixed_counter_placement_group_effect_template(
    text: str,
    *,
    card_name: str,
    source_is_permanent: bool | None,
) -> FixedCounterPlacementGroupTemplate | None:
    """Parse one bounded same-kind placement instruction on several subjects."""

    normalized = text.strip()
    if normalized.endswith("."):
        normalized = normalized[:-1]
    if not normalized or ";" in normalized or "\n" in normalized:
        return None
    raw_clauses = tuple(_CLAUSE_SEPARATOR.split(normalized))
    if not 2 <= len(raw_clauses) <= 3:
        return None

    placements: list[FixedCounterPlacementTemplate] = []
    optional_targets: list[bool] = []
    target_ordinals: list[str | None] = []
    source_indexes: list[int] = []
    target_indexes: list[int] = []
    for index, raw_clause in enumerate(raw_clauses):
        normalized_clause = _normalized_group_clause(
            raw_clause,
            first=index == 0,
        )
        if normalized_clause is None:
            return None
        clause, optional, ordinal, commander = normalized_clause
        placement = fixed_counter_placement_effect_template(
            f"{clause}.",
            card_name=card_name,
        )
        if placement is None or placement.subject not in {
            CounterPlacementSubject.SOURCE,
            CounterPlacementSubject.TARGET,
        }:
            return None
        if commander:
            target_spec = placement.target_spec
            if (
                placement.subject is not CounterPlacementSubject.TARGET
                or target_spec is None
                or target_spec.types_any != ("creature",)
                or target_spec.types_all
                or target_spec.subtypes_any
                or target_spec.keywords_all
            ):
                return None
            placement = replace(
                placement,
                target_spec=replace(target_spec, commander=True),
            )
        if placement.subject is CounterPlacementSubject.SOURCE:
            if optional or ordinal is not None or source_is_permanent is not True:
                return None
            source_indexes.append(index)
        else:
            target_indexes.append(index)
            target_ordinals.append(ordinal)
        placements.append(placement)
        optional_targets.append(optional)

    if len(source_indexes) > 1 or not target_indexes:
        return None
    if any(optional_targets[index] for index in range(len(placements) - 1)):
        return None
    if (
        optional_targets[-1]
        and placements[-1].subject is not CounterPlacementSubject.TARGET
    ):
        return None
    if len(
        {(placement.counter_name, placement.count) for placement in placements}
    ) != 1:
        return None

    globally_distinct = False
    if len(target_indexes) == 1:
        ordinal = target_ordinals[0]
        if ordinal == "a third":
            return None
        if ordinal is not None:
            placement_index = target_indexes[0]
            if not source_indexes:
                return None
            placement = placements[placement_index]
            if placement.target_spec is not None:
                placements[placement_index] = replace(
                    placement,
                    target_spec=replace(
                        placement.target_spec,
                        source_exclusion=True,
                    ),
                )
            else:
                placements[placement_index] = replace(
                    placement,
                    exclude_source=True,
                )
    elif any(value is not None for value in target_ordinals):
        expected = (
            (None, "another")
            if len(target_ordinals) == 2
            else (None, "another", "a third")
        )
        if tuple(target_ordinals) != expected:
            return None
        globally_distinct = True

    try:
        return FixedCounterPlacementGroupTemplate(
            placements=tuple(placements),
            optional_targets=tuple(optional_targets),
            globally_distinct=globally_distinct,
        )
    except ValueError:
        return None


__all__ = [
    "FixedCounterPlacementGroupTemplate",
    "fixed_counter_placement_group_effect_template",
]
