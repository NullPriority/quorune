from __future__ import annotations

"""Closed Oracle lowering for fixed counter-placement effects."""

from dataclasses import dataclass
from enum import Enum
import re
from typing import Any, Mapping

from ..attachment_references import (
    AttachmentReferenceKind,
    AttachmentReferenceSpec,
)
from ..affected_permanents import (
    AffectedPermanentSetSpec,
    PermanentControllerRelation,
)
from ..object_predicate import (
    ObjectQuerySpec,
    PermanentStatePredicateSpec,
)
from ..keyword_counters import keyword_counter_mechanic
from ..rules.source_references import (
    SourceReferenceSpec,
    source_self_permanent_type,
)
from .creature_subtypes import canonical_creature_subtype
from .direct_target import (
    DIRECT_PERMANENT_TYPES,
    DirectPermanentTargetSpec,
    direct_permanent_target_spec,
)
from .fixed_numbers import FIXED_COUNT_PATTERN, fixed_number


_COUNT = rf"(?:an|{FIXED_COUNT_PATTERN})"
_COUNTER_PLURAL = "counters"
FIXED_COUNTER_NAME_PATTERN = (
    r"[+-]\d+/[+-]\d+|"
    r"[A-Za-z][A-Za-z'-]*(?: [A-Za-z][A-Za-z'-]*){0,2}"
)
_PLACEMENT = re.compile(
    rf"put (?P<count>{_COUNT}) (?P<counter>{FIXED_COUNTER_NAME_PATTERN}) "
    r"(?P<plural>counter|counters) on (?P<subject>.+?)\.?",
    re.IGNORECASE,
)
_PERMANENT_TYPES = DIRECT_PERMANENT_TYPES


class CounterPlacementSubject(str, Enum):
    SOURCE = "source"
    TARGET = "target"
    ATTACHED = "attached"


class PlayerCounterPlacementSubject(str, Enum):
    CONTROLLER = "controller"
    TARGET = "target"
    EACH_PLAYER = "each-player"
    EACH_OPPONENT = "each-opponent"


@dataclass(frozen=True, slots=True)
class FixedCounterPlacementTemplate:
    """One mandatory fixed placement on the source or one direct target."""

    count: int
    counter_name: str
    subject: CounterPlacementSubject
    permanent_type: str | None = None
    creature_subtype: str | None = None
    commander: bool | None = None
    controller_relation: str = "any"
    exclude_source: bool = False
    attachment_relation: AttachmentReferenceKind | None = None
    target_spec: DirectPermanentTargetSpec | None = None

    def __post_init__(self) -> None:
        if type(self.count) is not int or self.count <= 0:
            raise ValueError("Counter placement count must be positive")
        if type(self.counter_name) is not str or not self.counter_name:
            raise ValueError("Counter placement name must be nonempty")
        if not isinstance(self.subject, CounterPlacementSubject):
            raise ValueError("Counter placement subject is unsupported")
        if self.target_spec is not None:
            if (
                not isinstance(self.target_spec, DirectPermanentTargetSpec)
                or self.subject is not CounterPlacementSubject.TARGET
                or self.permanent_type is not None
                or self.creature_subtype is not None
                or self.commander is not None
                or self.controller_relation != "any"
                or self.exclude_source
                or self.attachment_relation is not None
            ):
                raise ValueError(
                    "Typed counter targets cannot mix legacy target predicates"
                )
        if self.permanent_type not in {*_PERMANENT_TYPES, None}:
            raise ValueError("Counter placement permanent type is unsupported")
        if self.creature_subtype is not None and (
            canonical_creature_subtype(self.creature_subtype)
            != self.creature_subtype
        ):
            raise ValueError("Counter placement creature subtype is unsupported")
        if self.permanent_type is not None and self.creature_subtype is not None:
            raise ValueError("Counter placement requires one subject predicate")
        if self.commander is not None and self.commander is not True:
            raise ValueError("Counter placement commander predicate is unsupported")
        if self.commander is not None and (
            self.subject is not CounterPlacementSubject.TARGET
            or self.permanent_type != "creature"
            or self.creature_subtype is not None
        ):
            raise ValueError(
                "Counter placement commander predicate requires a creature target"
            )
        if self.controller_relation not in {"any", "you", "opponent"}:
            raise ValueError("Counter placement controller relation is unsupported")
        if self.subject is CounterPlacementSubject.SOURCE and (
            self.controller_relation != "any"
            or self.exclude_source
            or self.attachment_relation is not None
        ):
            raise ValueError("Source counter placement cannot add target predicates")
        if self.subject is CounterPlacementSubject.TARGET and (
            self.attachment_relation is not None
        ):
            raise ValueError("Target counter placement cannot use an attachment")
        if self.subject is CounterPlacementSubject.ATTACHED:
            if (
                not isinstance(
                    self.attachment_relation, AttachmentReferenceKind
                )
                or self.permanent_type is None
                or self.creature_subtype is not None
                or self.controller_relation != "any"
                or self.exclude_source
            ):
                raise ValueError(
                    "Attached counter placement requires one closed relation"
                )

    @property
    def template_id(self) -> str:
        subject = self.subject.value
        if self.target_spec is not None:
            version = (
                3
                if self.target_spec.uses_public_state
                else 2
                if self.target_spec.uses_compound_characteristics
                else 1
            )
            return (
                f"place-fixed-counter-{subject}-{self.target_spec.slug}-v{version}"
            )
        predicate = self.permanent_type or self.creature_subtype or "permanent"
        if self.commander:
            predicate = f"commander-{predicate}"
        if self.subject is CounterPlacementSubject.ATTACHED:
            assert self.attachment_relation is not None
            return (
                "place-fixed-counter-attached-"
                f"{self.attachment_relation.value}-{predicate}-v1"
            )
        relation = (
            f"-{self.controller_relation}"
            if self.controller_relation != "any"
            else ""
        )
        another = "-another" if self.exclude_source else ""
        return f"place-fixed-counter-{subject}-{predicate}{relation}{another}-v1"

    @property
    def effects(self) -> tuple[Mapping[str, Any], ...]:
        return (
            {
                "op": "place_counters",
                "card": self._card_reference,
                "counter": self.counter_name,
                "amount": self.count,
                "source": "$source",
            },
        )

    @property
    def target_schema(self) -> Mapping[str, Any] | None:
        if self.subject is not CounterPlacementSubject.TARGET:
            return None
        if self.target_spec is not None:
            return self.target_spec.to_target_schema()
        schema: dict[str, Any] = {
            "zones": ["battlefield"],
            "categories": ["permanent"],
            "count": 1,
        }
        if self.permanent_type not in {None, "permanent"}:
            schema["types_any"] = [self.permanent_type]
        elif self.creature_subtype is not None:
            schema["subtypes_any"] = [self.creature_subtype]
        if self.controller_relation != "any":
            schema["controller_relation"] = self.controller_relation
        if self.commander is not None:
            schema["commander"] = self.commander
        if self.exclude_source:
            schema["source_exclusion"] = True
        return schema

    @property
    def mechanics(self) -> tuple[str, ...]:
        mechanics = (
            ("cr-122-counters",)
            if self.subject is not CounterPlacementSubject.TARGET
            else ("cr-122-counters", "cr-115-targets")
        )
        keyword = keyword_counter_mechanic(self.counter_name)
        return mechanics + ((keyword,) if keyword is not None else ())

    @property
    def _card_reference(self) -> str | Mapping[str, Any]:
        if self.subject is CounterPlacementSubject.SOURCE:
            return "$source"
        if self.subject is CounterPlacementSubject.TARGET:
            return "$target.0"
        assert self.attachment_relation is not None
        assert self.permanent_type is not None
        return AttachmentReferenceSpec(
            relation=self.attachment_relation,
            required_card_type=self.permanent_type,
        ).to_dict()

    def compiled(
        self,
    ) -> tuple[
        str,
        tuple[Mapping[str, Any], ...],
        Mapping[str, Any] | None,
        tuple[str, ...],
    ]:
        return (
            self.template_id,
            self.effects,
            self.target_schema,
            self.mechanics,
        )


@dataclass(frozen=True, slots=True)
class FixedCounterPlacementBatchTemplate:
    """Two or three fixed counter kinds placed on one shared subject."""

    placements: tuple[tuple[str, int], ...]
    subject_template: FixedCounterPlacementTemplate

    def __post_init__(self) -> None:
        placements = tuple(self.placements)
        if not 2 <= len(placements) <= 3:
            raise ValueError("Counter batches require two or three placements")
        normalized: list[tuple[str, int]] = []
        names: set[str] = set()
        for counter_name, amount in placements:
            name = (
                " ".join(counter_name.casefold().split())
                if type(counter_name) is str
                else ""
            )
            if not name or type(amount) is not int or amount <= 0:
                raise ValueError(
                    "Counter batch entries require a name and positive amount"
                )
            if name in names:
                raise ValueError("Counter batch kinds must be distinct")
            names.add(name)
            normalized.append((name, amount))
        if not isinstance(self.subject_template, FixedCounterPlacementTemplate):
            raise ValueError("Counter batches require one closed subject")
        object.__setattr__(self, "placements", tuple(normalized))

    @property
    def template_id(self) -> str:
        return (
            "place-fixed-counter-batch-"
            f"{len(self.placements)}-"
            f"{self.subject_template.template_id.removesuffix('-v1')}-v1"
        )

    @property
    def effects(self) -> tuple[Mapping[str, Any], ...]:
        return (
            {
                "op": "place_counter_batch",
                "card": self.subject_template._card_reference,
                "placements": [
                    {"counter": counter_name, "amount": amount}
                    for counter_name, amount in self.placements
                ],
                "source": "$source",
            },
        )

    @property
    def target_schema(self) -> Mapping[str, Any] | None:
        return self.subject_template.target_schema

    @property
    def mechanics(self) -> tuple[str, ...]:
        mechanics = list(self.subject_template.mechanics)
        for counter_name, _amount in self.placements:
            keyword = keyword_counter_mechanic(counter_name)
            if keyword is not None and keyword not in mechanics:
                mechanics.append(keyword)
        return tuple(mechanics)

    def compiled(
        self,
    ) -> tuple[
        str,
        tuple[Mapping[str, Any], ...],
        Mapping[str, Any] | None,
        tuple[str, ...],
    ]:
        return (
            self.template_id,
            self.effects,
            self.target_schema,
            self.mechanics,
        )


@dataclass(frozen=True, slots=True)
class ExistingTargetCounterPlacementTemplate:
    """One fixed placement on the already-declared target at index zero."""

    count: int
    counter_name: str

    def __post_init__(self) -> None:
        if type(self.count) is not int or self.count <= 0:
            raise ValueError("Counter placement count must be positive")
        if type(self.counter_name) is not str or not self.counter_name:
            raise ValueError("Counter placement name must be nonempty")

    @property
    def effects(self) -> tuple[Mapping[str, Any], ...]:
        return (
            {
                "op": "place_counters",
                "card": "$target.0",
                "counter": self.counter_name,
                "amount": self.count,
                "source": "$source",
            },
        )

    def compiled(
        self,
    ) -> tuple[
        str,
        tuple[Mapping[str, Any], ...],
        None,
        tuple[str, ...],
    ]:
        return (
            "place-fixed-counter-existing-target-v1",
            self.effects,
            None,
            (
                "cr-122-counters",
                *(
                    (keyword,)
                    if (keyword := keyword_counter_mechanic(self.counter_name))
                    is not None
                    else ()
                ),
            ),
        )


@dataclass(frozen=True, slots=True)
class FixedCounterPlacementTargetSetTemplate:
    """One fixed placement on each member of an optional target set."""

    count: int
    counter_name: str
    maximum_targets: int
    permanent_type: str
    controller_relation: str = "any"
    exclude_creature: bool = False
    state_predicate: PermanentStatePredicateSpec | None = None

    def __post_init__(self) -> None:
        if type(self.count) is not int or self.count <= 0:
            raise ValueError("Counter-target placement count must be positive")
        if type(self.counter_name) is not str:
            raise ValueError("Counter-target placement name must be nonempty")
        normalized = " ".join(self.counter_name.casefold().split())
        if not normalized:
            raise ValueError("Counter-target placement name must be nonempty")
        object.__setattr__(self, "counter_name", normalized)
        if type(self.maximum_targets) is not int or self.maximum_targets <= 0:
            raise ValueError("Counter-target maximum must be positive")
        if self.permanent_type not in _PERMANENT_TYPES:
            raise ValueError("Counter-target permanent type is unsupported")
        if self.controller_relation not in {"any", "you", "opponent"}:
            raise ValueError("Counter-target controller relation is unsupported")
        if type(self.exclude_creature) is not bool or (
            self.exclude_creature and self.permanent_type != "artifact"
        ):
            raise ValueError("Counter-target negative type predicate is unsupported")
        if self.state_predicate is not None and (
            not isinstance(self.state_predicate, PermanentStatePredicateSpec)
            or self.state_predicate.tapped is not True
            or self.state_predicate.entered_this_turn
            or self.state_predicate.counter_name is not None
            or self.permanent_type != "creature"
        ):
            raise ValueError("Counter-target public-state predicate is unsupported")

    @property
    def template_id(self) -> str:
        negative = "noncreature-" if self.exclude_creature else ""
        relation = (
            f"-{self.controller_relation}"
            if self.controller_relation != "any"
            else ""
        )
        state = "-tapped" if self.state_predicate is not None else ""
        return (
            f"place-fixed-counter-target-set-{self.maximum_targets}-"
            f"{negative}{self.permanent_type}{relation}{state}-"
            f"v{2 if self.state_predicate is not None else 1}"
        )

    @property
    def effects(self) -> tuple[Mapping[str, Any], ...]:
        return (
            {
                "op": "place_counters_on_targets",
                "cards": "$targets",
                "maximum_targets": self.maximum_targets,
                "counter": self.counter_name,
                "amount": self.count,
                "source": "$source",
            },
        )

    @property
    def target_schema(self) -> Mapping[str, Any]:
        schema: dict[str, Any] = {
            "zones": ["battlefield"],
            "categories": ["permanent"],
            "up_to": self.maximum_targets,
        }
        if self.permanent_type != "permanent":
            schema["types_any"] = [self.permanent_type]
        if self.exclude_creature:
            schema["types_none"] = ["creature"]
        if self.controller_relation != "any":
            schema["controller_relation"] = self.controller_relation
        if self.state_predicate is not None:
            schema["state_predicate"] = self.state_predicate.to_dict()
        return schema

    @property
    def mechanics(self) -> tuple[str, ...]:
        keyword = keyword_counter_mechanic(self.counter_name)
        return (
            "cr-122-counters",
            "cr-115-targets",
            *((keyword,) if keyword is not None else ()),
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


@dataclass(frozen=True, slots=True)
class SupportCounterPlacementTemplate:
    """One fixed Support N instruction with source-context target semantics."""

    maximum_targets: int
    source_is_permanent: bool

    def __post_init__(self) -> None:
        if type(self.maximum_targets) is not int or self.maximum_targets <= 0:
            raise ValueError("Support maximum must be a positive exact integer")
        if type(self.source_is_permanent) is not bool:
            raise ValueError("Support source context must be explicit")

    @property
    def template_id(self) -> str:
        context = "permanent" if self.source_is_permanent else "spell"
        return f"support-fixed-{context}-{self.maximum_targets}-v1"

    @property
    def effects(self) -> tuple[Mapping[str, Any], ...]:
        return (
            {
                "op": "place_counters_on_targets",
                "cards": "$targets",
                "maximum_targets": self.maximum_targets,
                "counter": "+1/+1",
                "amount": 1,
                "source": "$source",
            },
        )

    @property
    def target_schema(self) -> Mapping[str, Any]:
        schema: dict[str, Any] = {
            "zones": ["battlefield"],
            "categories": ["permanent"],
            "types_any": ["creature"],
            "up_to": self.maximum_targets,
            "support_source_context": (
                "permanent" if self.source_is_permanent else "spell"
            ),
        }
        if self.source_is_permanent:
            schema["source_exclusion"] = True
        return schema

    @property
    def mechanics(self) -> tuple[str, ...]:
        return ("support", "cr-122-counters", "cr-115-targets")

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
class FixedPlayerCounterPlacementTemplate:
    """One mandatory fixed placement on a closed player relation."""

    count: int
    counter_name: str
    subject: PlayerCounterPlacementSubject
    player_relation: str = "any"

    def __post_init__(self) -> None:
        if type(self.count) is not int or self.count <= 0:
            raise ValueError("Player counter placement count must be positive")
        if type(self.counter_name) is not str:
            raise ValueError(
                "Player counter placement name must be nonempty"
            )
        normalized = " ".join(self.counter_name.casefold().split())
        if not normalized:
            raise ValueError(
                "Player counter placement name must be nonempty"
            )
        object.__setattr__(self, "counter_name", normalized)
        if not isinstance(self.subject, PlayerCounterPlacementSubject):
            raise ValueError("Player counter placement subject is unsupported")
        if self.player_relation not in {"any", "opponent"}:
            raise ValueError("Player counter relation is unsupported")
        if self.subject is not PlayerCounterPlacementSubject.TARGET and (
            self.player_relation != "any"
        ):
            raise ValueError(
                "Only targeted player counters accept a player relation"
            )

    @property
    def template_id(self) -> str:
        relation = (
            f"-{self.player_relation}"
            if self.subject is PlayerCounterPlacementSubject.TARGET
            else ""
        )
        return (
            f"place-fixed-player-counter-{self.subject.value}{relation}-v1"
        )

    @property
    def effects(self) -> tuple[Mapping[str, Any], ...]:
        effect: dict[str, Any] = {
            "op": "place_player_counters",
            "subjects": self.subject.value,
            "counter": self.counter_name,
            "amount": self.count,
            "source": "$source",
        }
        if self.subject is PlayerCounterPlacementSubject.TARGET:
            effect["target"] = "$target.0"
        return (effect,)

    @property
    def target_schema(self) -> Mapping[str, Any] | None:
        if self.subject is not PlayerCounterPlacementSubject.TARGET:
            return None
        schema: dict[str, Any] = {
            "zones": ["player"],
            "categories": ["player"],
            "count": 1,
        }
        if self.player_relation != "any":
            schema["player_relation"] = self.player_relation
        return schema

    @property
    def mechanics(self) -> tuple[str, ...]:
        return (
            ("cr-122-counters", "cr-115-targets")
            if self.subject is PlayerCounterPlacementSubject.TARGET
            else ("cr-122-counters",)
        )

    def compiled(
        self,
    ) -> tuple[
        str,
        tuple[Mapping[str, Any], ...],
        Mapping[str, Any] | None,
        tuple[str, ...],
    ]:
        return (
            self.template_id,
            self.effects,
            self.target_schema,
            self.mechanics,
        )


@dataclass(frozen=True, slots=True)
class FixedCounterPlacementSetTemplate:
    """One mandatory fixed placement on one closed battlefield set."""

    count: int
    counter_name: str
    spec: AffectedPermanentSetSpec
    target_relation: str | None = None

    def __post_init__(self) -> None:
        if type(self.count) is not int or self.count <= 0:
            raise ValueError("Counter-set placement count must be positive")
        if type(self.counter_name) is not str:
            raise ValueError("Counter-set placement name must be nonempty")
        normalized = " ".join(self.counter_name.casefold().split())
        if not normalized:
            raise ValueError("Counter-set placement name must be nonempty")
        object.__setattr__(self, "counter_name", normalized)
        if not isinstance(self.spec, AffectedPermanentSetSpec):
            raise ValueError("Counter-set placement requires a typed set")
        if not fixed_counter_set_spec_is_closed(self.spec):
            raise ValueError("Counter-set placement predicate is unsupported")
        if self.target_relation not in {None, "any", "opponent"}:
            raise ValueError("Counter-set player target relation is unsupported")
        needs_target = (
            self.spec.controller_relation
            is PermanentControllerRelation.TARGET_PLAYER
        )
        if needs_target is not (self.target_relation is not None):
            raise ValueError(
                "Counter-set target relation contradicts its affected set"
            )

    @property
    def template_id(self) -> str:
        return (
            f"place-fixed-counter-set-{self.spec.fingerprint[:16]}-"
            f"{self.target_relation or 'untargeted'}-v1"
        )

    @property
    def effects(self) -> tuple[Mapping[str, Any], ...]:
        return (
            {
                "op": "place_counters_on_set",
                "source": "$source",
                "set": self.spec.to_dict(),
                "counter": self.counter_name,
                "amount": self.count,
            },
        )

    @property
    def target_schema(self) -> Mapping[str, Any] | None:
        if self.target_relation is None:
            return None
        return {
            "zones": ["player"],
            "categories": ["player"],
            "count": 1,
            "player_relation": self.target_relation,
        }

    @property
    def mechanics(self) -> tuple[str, ...]:
        mechanics = (
            ("cr-122-counters", "cr-115-targets")
            if self.target_relation is not None
            else ("cr-122-counters",)
        )
        keyword = keyword_counter_mechanic(self.counter_name)
        return mechanics + ((keyword,) if keyword is not None else ())

    def compiled(
        self,
    ) -> tuple[
        str,
        tuple[Mapping[str, Any], ...],
        Mapping[str, Any] | None,
        tuple[str, ...],
    ]:
        return (
            self.template_id,
            self.effects,
            self.target_schema,
            self.mechanics,
        )


def fixed_counter_placement_effect_template(
    text: str,
    *,
    card_name: str,
    source_attachment_relation: AttachmentReferenceKind | None = None,
) -> FixedCounterPlacementTemplate | None:
    """Parse only one closed, mandatory, positive fixed placement clause."""

    match = _PLACEMENT.fullmatch(text.strip())
    if match is None:
        return None
    count = fixed_number(match.group("count"))
    if count <= 0 or (match.group("plural").casefold() == "counter") != (
        count == 1
    ):
        return None
    counter_name = " ".join(match.group("counter").casefold().split())
    subject = " ".join(match.group("subject").split())
    source_permanent_type = source_self_permanent_type(subject)
    if source_permanent_type is not None:
        return FixedCounterPlacementTemplate(
            count=count,
            counter_name=counter_name,
            subject=CounterPlacementSubject.SOURCE,
            permanent_type=source_permanent_type,
        )
    if SourceReferenceSpec(card_name).matches(subject):
        return FixedCounterPlacementTemplate(
            count=count,
            counter_name=counter_name,
            subject=CounterPlacementSubject.SOURCE,
        )
    attached = re.fullmatch(
        r"(?P<relation>enchanted|equipped|fortified) "
        r"(?P<kind>artifact|battle|creature|enchantment|land|permanent|"
        r"planeswalker)",
        subject,
        re.IGNORECASE,
    )
    if attached is not None:
        try:
            relation = AttachmentReferenceKind[
                attached.group("relation").upper()
            ]
        except KeyError:
            return None
        if relation is not source_attachment_relation:
            return None
        return FixedCounterPlacementTemplate(
            count=count,
            counter_name=counter_name,
            subject=CounterPlacementSubject.ATTACHED,
            permanent_type=attached.group("kind").casefold(),
            attachment_relation=relation,
        )
    target = direct_permanent_target_spec(subject)
    if target is None:
        return None
    return FixedCounterPlacementTemplate(
        count=count,
        counter_name=counter_name,
        subject=CounterPlacementSubject.TARGET,
        target_spec=target,
    )


def fixed_counter_placement_batch_effect_template(
    text: str,
    *,
    card_name: str,
    source_attachment_relation: AttachmentReferenceKind | None = None,
) -> FixedCounterPlacementBatchTemplate | None:
    """Parse one closed simultaneous placement of distinct fixed counters."""

    normalized = text.strip()
    if normalized.endswith("."):
        normalized = normalized[:-1]
    match = re.fullmatch(
        r"put (?P<placements>.+?) on (?P<subject>.+)",
        normalized,
        re.IGNORECASE,
    )
    if match is None:
        return None
    raw_entries = re.split(
        r",\s*(?:and\s+)?|\s+and\s+",
        match.group("placements"),
        flags=re.IGNORECASE,
    )
    if not 2 <= len(raw_entries) <= 3:
        return None
    entry_pattern = re.compile(
        rf"(?P<count>{_COUNT}) (?P<counter>{FIXED_COUNTER_NAME_PATTERN}) "
        r"(?P<plural>counter|counters)",
        re.IGNORECASE,
    )
    placements: list[tuple[str, int]] = []
    for raw_entry in raw_entries:
        entry = entry_pattern.fullmatch(raw_entry.strip())
        if entry is None:
            return None
        count = fixed_number(entry.group("count"))
        if count <= 0 or (entry.group("plural").casefold() == "counter") != (
            count == 1
        ):
            return None
        placements.append(
            (" ".join(entry.group("counter").casefold().split()), count)
        )
    if len({name for name, _amount in placements}) != len(placements):
        return None
    first_name = placements[0][0]
    subject_template = fixed_counter_placement_effect_template(
        f"Put a {first_name} counter on {match.group('subject')}.",
        card_name=card_name,
        source_attachment_relation=source_attachment_relation,
    )
    if subject_template is None:
        return None
    if subject_template.subject not in {
        CounterPlacementSubject.SOURCE,
        CounterPlacementSubject.TARGET,
    }:
        return None
    return FixedCounterPlacementBatchTemplate(
        placements=tuple(placements),
        subject_template=subject_template,
    )


def existing_target_counter_placement_effect_template(
    text: str,
) -> ExistingTargetCounterPlacementTemplate | None:
    """Parse a mandatory fixed placement referring to an established target."""

    match = _PLACEMENT.fullmatch(text.strip())
    if match is None or match.group("subject").casefold() != "it":
        return None
    count = fixed_number(match.group("count"))
    if count <= 0 or (match.group("plural").casefold() == "counter") != (
        count == 1
    ):
        return None
    return ExistingTargetCounterPlacementTemplate(
        count=count,
        counter_name=" ".join(match.group("counter").casefold().split()),
    )


_TARGET_SET_PERMANENT_TYPES = {
    "artifact": "artifact",
    "artifacts": "artifact",
    "battle": "battle",
    "battles": "battle",
    "creature": "creature",
    "creatures": "creature",
    "enchantment": "enchantment",
    "enchantments": "enchantment",
    "land": "land",
    "lands": "land",
    "permanent": "permanent",
    "permanents": "permanent",
    "planeswalker": "planeswalker",
    "planeswalkers": "planeswalker",
}


def fixed_counter_placement_target_set_effect_template(
    text: str,
) -> FixedCounterPlacementTargetSetTemplate | None:
    """Parse one fixed placement on each of up to N direct targets."""

    match = _PLACEMENT.fullmatch(text.strip())
    if match is None:
        return None
    count = fixed_number(match.group("count"))
    if count <= 0 or (match.group("plural").casefold() == "counter") != (
        count == 1
    ):
        return None
    subject = " ".join(match.group("subject").casefold().split())
    target = re.fullmatch(
        rf"(?:each of )?up to (?P<maximum>{_COUNT}) target "
        r"(?P<noncreature>noncreature )?"
        r"(?P<tapped>tapped )?"
        r"(?P<kind>artifact|artifacts|battle|battles|creature|creatures|"
        r"enchantment|enchantments|land|lands|permanent|permanents|"
        r"planeswalker|planeswalkers)"
        r"(?P<relation> you control| an opponent controls| you don't control)?",
        subject,
        re.IGNORECASE,
    )
    if target is None:
        return None
    maximum = fixed_number(target.group("maximum"))
    kind_word = target.group("kind").casefold()
    singular = not kind_word.endswith("s")
    if maximum <= 0 or singular is not (maximum == 1):
        return None
    permanent_type = _TARGET_SET_PERMANENT_TYPES[kind_word]
    exclude_creature = bool(target.group("noncreature"))
    if exclude_creature and permanent_type != "artifact":
        return None
    relation = (target.group("relation") or "").casefold()
    tapped = bool(target.group("tapped"))
    if tapped and permanent_type != "creature":
        return None
    return FixedCounterPlacementTargetSetTemplate(
        count=count,
        counter_name=match.group("counter"),
        maximum_targets=maximum,
        permanent_type=permanent_type,
        controller_relation=(
            "you"
            if relation == " you control"
            else "opponent"
            if relation
            else "any"
        ),
        exclude_creature=exclude_creature,
        state_predicate=(
            PermanentStatePredicateSpec(tapped=True) if tapped else None
        ),
    )


def support_counter_placement_effect_template(
    text: str,
    *,
    source_is_permanent: bool,
) -> SupportCounterPlacementTemplate | None:
    """Parse one ordinary fixed positive Support N keyword action."""

    if type(source_is_permanent) is not bool:
        raise ValueError("Support source context must be explicit")
    match = re.fullmatch(
        rf"support (?P<maximum>{_COUNT})\.?",
        text.strip(),
        re.IGNORECASE,
    )
    if match is None:
        return None
    maximum = fixed_number(match.group("maximum"))
    if maximum <= 0:
        return None
    return SupportCounterPlacementTemplate(
        maximum_targets=maximum,
        source_is_permanent=source_is_permanent,
    )


_SET_COLOR_WORDS = {
    "white": "W",
    "blue": "U",
    "black": "B",
    "red": "R",
    "green": "G",
}
FIXED_COUNTER_SET_KEYWORDS = frozenset(
    {"flying", "lifelink", "menace", "trample", "vigilance"}
)
_FIXED_COUNTER_SET_TYPE_SHAPES = frozenset(
    {
        (),
        ("artifact",),
        ("battle",),
        ("creature",),
        ("enchantment",),
        ("land",),
        ("planeswalker",),
        ("artifact", "creature"),
        ("creature", "land"),
    }
)
_SET_NONCREATURE_SUBTYPES = {
    "equipment": "equipment",
    "saga": "saga",
}


def fixed_counter_set_spec_is_closed(
    spec: AffectedPermanentSetSpec,
) -> bool:
    """Return whether a set uses only the reviewed compiler grammar."""

    if not isinstance(spec, AffectedPermanentSetSpec):
        return False
    query = spec.query
    type_shape = tuple(query.types_all)
    if type_shape not in _FIXED_COUNTER_SET_TYPE_SHAPES:
        return False
    if (
        query.types_any
        or query.excluded_types
        or query.excluded_subtypes
        or query.colors_all
        or query.colorless is not None
    ):
        return False
    subtypes = tuple(query.subtypes_all)
    if len(subtypes) > 1:
        return False
    if subtypes:
        subtype = subtypes[0]
        creature_subtype = canonical_creature_subtype(subtype)
        if creature_subtype is not None:
            if type_shape not in {(), ("creature",)}:
                return False
        elif subtype not in {"equipment", "saga"} or type_shape:
            return False
    subtype_disjunction = tuple(query.subtypes_any)
    if subtype_disjunction:
        if subtypes or type_shape or not 2 <= len(subtype_disjunction) <= 8:
            return False
        if any(
            canonical_creature_subtype(value) != value
            for value in subtype_disjunction
        ):
            return False
    if tuple(query.supertypes_all) not in {(), ("legendary",)}:
        return False
    if query.supertypes_all and type_shape not in {
        ("creature",),
        ("planeswalker",),
    }:
        return False
    if len(query.colors_any) > 1 or not set(query.colors_any).issubset(
        {"W", "U", "B", "R", "G"}
    ):
        return False
    if query.colors_any and type_shape not in {
        ("creature",),
        ("planeswalker",),
    }:
        return False
    if len(query.keywords_all) > 1 or not set(query.keywords_all).issubset(
        FIXED_COUNTER_SET_KEYWORDS
    ):
        return False
    if query.keywords_all and type_shape != ("creature",):
        return False
    if (query.token is not None or query.tapped is not None) and (
        type_shape != ("creature",)
    ):
        return False
    state = query.state_predicate
    if state is not None:
        if state.tapped is not None or sum(
            (state.entered_this_turn, state.counter_name is not None)
        ) != 1:
            return False
        if state.entered_this_turn and not (
            type_shape == ("creature",) or subtype_disjunction
        ):
            return False
        if state.counter_name is not None and not (
            type_shape == ("creature",) or subtypes or subtype_disjunction
        ):
            return False
    qualifier_count = sum(
        (
            bool(query.supertypes_all),
            bool(query.colors_any),
            bool(query.keywords_all),
            query.token is not None,
            query.tapped is not None,
        )
    )
    return qualifier_count <= 1


def _fixed_counter_set_state_clause(
    phrase: str,
) -> tuple[str, PermanentStatePredicateSpec | None] | None:
    """Remove one closed public-state suffix from an affected-set phrase."""

    counter_state = re.fullmatch(
        rf"(?P<body>.+) with (?:a|an) "
        rf"(?P<counter>{FIXED_COUNTER_NAME_PATTERN}) "
        r"counter on it",
        phrase,
        re.IGNORECASE,
    )
    if counter_state is not None:
        try:
            return (
                counter_state.group("body"),
                PermanentStatePredicateSpec(
                    counter_name=counter_state.group("counter"),
                    minimum_counter_count=1,
                ),
            )
        except ValueError:
            return None
    for suffix in (
        " that entered the battlefield this turn",
        " that entered this turn",
    ):
        if phrase.endswith(suffix):
            return (
                phrase[: -len(suffix)],
                PermanentStatePredicateSpec(entered_this_turn=True),
            )
    return phrase, None


def _fixed_counter_set_subtype_predicates(
    phrase: str,
) -> dict[str, Any] | None:
    """Return one closed creature-subtype predicate from an affected set."""

    raw_subtypes = tuple(
        value.strip()
        for value in re.split(r",\s*(?:or\s+)?|\s+or\s+", phrase)
        if value.strip()
    )
    if len(raw_subtypes) > 1:
        subtypes = tuple(
            canonical_creature_subtype(value) for value in raw_subtypes
        )
        if any(value is None for value in subtypes):
            return None
        return {"subtypes_any": subtypes}
    creature_match = re.fullmatch(
        r"(?P<subtype>[a-z][a-z' -]*?)(?P<creature> creature)?",
        phrase,
    )
    if creature_match is None:
        return None
    subtype = canonical_creature_subtype(creature_match.group("subtype"))
    if subtype is None:
        return None
    return {
        "subtypes_all": (subtype,),
        **(
            {"types_all": ("creature",)}
            if creature_match.group("creature")
            else {}
        ),
    }


def _fixed_counter_set_query(
    subject: str,
) -> tuple[AffectedPermanentSetSpec, str | None] | None:
    phrase = " ".join(subject.casefold().split())
    if not phrase.startswith("each "):
        return None
    phrase = phrase[5:]

    keyword: str | None = None
    keyword_match = re.fullmatch(
        r"(?P<body>.+) with (?P<keyword>"
        + "|".join(sorted(FIXED_COUNTER_SET_KEYWORDS))
        + r")",
        phrase,
    )
    if keyword_match is not None:
        phrase = keyword_match.group("body")
        keyword = keyword_match.group("keyword")

    state_clause = _fixed_counter_set_state_clause(phrase)
    if state_clause is None:
        return None
    phrase, state_predicate = state_clause

    relation = PermanentControllerRelation.ANY
    target_controller: str | None = None
    target_relation: str | None = None
    controller_suffixes = (
        (
            " target opponent controls",
            PermanentControllerRelation.TARGET_PLAYER,
            "$target.0",
            "opponent",
        ),
        (
            " target player controls",
            PermanentControllerRelation.TARGET_PLAYER,
            "$target.0",
            "any",
        ),
        (
            " each opponent controls",
            PermanentControllerRelation.OPPONENTS,
            None,
            None,
        ),
        (
            " your opponents control",
            PermanentControllerRelation.OPPONENTS,
            None,
            None,
        ),
        (
            " opponents control",
            PermanentControllerRelation.OPPONENTS,
            None,
            None,
        ),
        (
            " you don't control",
            PermanentControllerRelation.OPPONENTS,
            None,
            None,
        ),
        (
            " you control",
            PermanentControllerRelation.ACTOR,
            None,
            None,
        ),
    )
    for suffix, candidate, target, target_kind in controller_suffixes:
        if phrase.endswith(suffix):
            phrase = phrase[: -len(suffix)]
            relation = candidate
            target_controller = target
            target_relation = target_kind
            break

    exclude_source = phrase.startswith("other ")
    if exclude_source:
        phrase = phrase[6:]
    kwargs: dict[str, Any] = {
        "zones": ("battlefield",),
        "state_predicate": state_predicate,
    }

    exact_types: dict[str, tuple[str, ...]] = {
        "permanent": (),
        "artifact": ("artifact",),
        "battle": ("battle",),
        "creature": ("creature",),
        "enchantment": ("enchantment",),
        "land": ("land",),
        "planeswalker": ("planeswalker",),
        "artifact creature": ("artifact", "creature"),
        "land creature": ("creature", "land"),
    }
    if phrase in exact_types:
        kwargs["types_all"] = exact_types[phrase]
    elif phrase in {"token creature", "creature token"}:
        kwargs["types_all"] = ("creature",)
        kwargs["token"] = True
    elif phrase == "nontoken creature":
        kwargs["types_all"] = ("creature",)
        kwargs["token"] = False
    else:
        quality = re.fullmatch(
            r"(?P<quality>legendary|tapped|untapped|white|blue|black|red|green) "
            r"(?P<kind>creature|planeswalker)",
            phrase,
        )
        if quality is not None:
            kwargs["types_all"] = (quality.group("kind"),)
            value = quality.group("quality")
            if value == "legendary":
                kwargs["supertypes_all"] = ("legendary",)
            elif value in {"tapped", "untapped"}:
                kwargs["tapped"] = value == "tapped"
            else:
                kwargs["colors_any"] = (_SET_COLOR_WORDS[value],)
        elif phrase in _SET_NONCREATURE_SUBTYPES:
            kwargs["subtypes_all"] = (_SET_NONCREATURE_SUBTYPES[phrase],)
        else:
            subtype_predicates = _fixed_counter_set_subtype_predicates(phrase)
            if subtype_predicates is None:
                return None
            kwargs.update(subtype_predicates)

    if keyword is not None:
        if keyword not in FIXED_COUNTER_SET_KEYWORDS or kwargs.get("types_all") != (
            "creature",
        ):
            return None
        kwargs["keywords_all"] = (keyword,)
    try:
        return (
            AffectedPermanentSetSpec(
                query=ObjectQuerySpec(**kwargs),
                controller_relation=relation,
                target_controller=target_controller,
                exclude_source=exclude_source,
            ),
            target_relation,
        )
    except ValueError:
        return None


def fixed_counter_placement_set_effect_template(
    text: str,
) -> FixedCounterPlacementSetTemplate | None:
    """Parse one mandatory fixed placement on a closed permanent set."""

    match = _PLACEMENT.fullmatch(text.strip())
    if match is None:
        return None
    count = fixed_number(match.group("count"))
    if count <= 0 or (match.group("plural").casefold() == "counter") != (
        count == 1
    ):
        return None
    parsed = _fixed_counter_set_query(match.group("subject"))
    if parsed is None:
        return None
    spec, target_relation = parsed
    try:
        return FixedCounterPlacementSetTemplate(
            count=count,
            counter_name=match.group("counter"),
            spec=spec,
            target_relation=target_relation,
        )
    except ValueError:
        return None


_PLAYER_COUNTER_WORDING = re.compile(
    rf"(?P<subject>you|target player|target opponent|each player|each opponent) "
    rf"(?P<verb>get|gets) (?P<count>{_COUNT}) "
    rf"(?P<counter>{FIXED_COUNTER_NAME_PATTERN}) "
    rf"(?P<plural>counter|counters)\.?",
    re.IGNORECASE,
)
_PLAYER_COUNTER_SYMBOLS = re.compile(
    rf"(?P<subject>you|target player|target opponent|each player|each opponent) "
    rf"(?P<verb>get|gets) (?:(?P<count>{_COUNT}) )?"
    r"(?P<symbols>(?:\{E\})+|(?:\{TK\})+)"
    r"(?: \((?P<explanation>[^()]*)\))?\.?",
    re.IGNORECASE,
)


def _player_counter_subject(
    subject: str,
    verb: str,
) -> tuple[PlayerCounterPlacementSubject, str] | None:
    normalized = " ".join(subject.casefold().split())
    expected_verb = "get" if normalized == "you" else "gets"
    if verb.casefold() != expected_verb:
        return None
    return {
        "you": (PlayerCounterPlacementSubject.CONTROLLER, "any"),
        "target player": (PlayerCounterPlacementSubject.TARGET, "any"),
        "target opponent": (
            PlayerCounterPlacementSubject.TARGET,
            "opponent",
        ),
        "each player": (PlayerCounterPlacementSubject.EACH_PLAYER, "any"),
        "each opponent": (
            PlayerCounterPlacementSubject.EACH_OPPONENT,
            "any",
        ),
    }.get(normalized)


def _validated_symbol_explanation(
    explanation: str | None,
    *,
    count: int,
    counter_name: str,
    explicit_count: bool,
) -> bool:
    if explanation is None:
        return True
    match = re.fullmatch(
        rf"(?:(?P<count>{_COUNT}) )?(?P<counter>energy|ticket) "
        r"(?P<plural>counter|counters)",
        explanation.strip(),
        re.IGNORECASE,
    )
    if match is None:
        return False
    raw_count = match.group("count")
    if raw_count is None:
        return (
            explicit_count
            and count > 1
            and match.group("counter").casefold() == counter_name
            and match.group("plural").casefold() == _COUNTER_PLURAL
        )
    explained_count = fixed_number(raw_count)
    return (
        explained_count == count
        and match.group("counter").casefold() == counter_name
        and (match.group("plural").casefold() == "counter") == (count == 1)
    )


def fixed_player_counter_placement_effect_template(
    text: str,
) -> FixedPlayerCounterPlacementTemplate | None:
    """Parse one mandatory fixed player-counter placement instruction."""

    normalized = re.sub(r"\s+([.,])", r"\1", text.strip())
    symbol_match = _PLAYER_COUNTER_SYMBOLS.fullmatch(normalized)
    if symbol_match is not None:
        subject = _player_counter_subject(
            symbol_match.group("subject"), symbol_match.group("verb")
        )
        if subject is None:
            return None
        symbols = symbol_match.group("symbols").upper()
        symbol = "{TK}" if symbols.startswith("{TK}") else "{E}"
        if symbols != symbol * (symbols.count(symbol)):
            return None
        explicit = symbol_match.group("count")
        count = (
            fixed_number(explicit)
            if explicit is not None
            else symbols.count(symbol)
        )
        if count <= 0 or (explicit is not None and symbols.count(symbol) != 1):
            return None
        counter_name = "ticket" if symbol == "{TK}" else "energy"
        if not _validated_symbol_explanation(
            symbol_match.group("explanation"),
            count=count,
            counter_name=counter_name,
            explicit_count=explicit is not None,
        ):
            return None
        return FixedPlayerCounterPlacementTemplate(
            count=count,
            counter_name=counter_name,
            subject=subject[0],
            player_relation=subject[1],
        )

    word_match = _PLAYER_COUNTER_WORDING.fullmatch(normalized)
    if word_match is None:
        return None
    subject = _player_counter_subject(
        word_match.group("subject"), word_match.group("verb")
    )
    count = fixed_number(word_match.group("count"))
    if (
        subject is None
        or count <= 0
        or (word_match.group("plural").casefold() == "counter") != (count == 1)
    ):
        return None
    return FixedPlayerCounterPlacementTemplate(
        count=count,
        counter_name=word_match.group("counter"),
        subject=subject[0],
        player_relation=subject[1],
    )


__all__ = [
    "CounterPlacementSubject",
    "ExistingTargetCounterPlacementTemplate",
    "FIXED_COUNTER_NAME_PATTERN",
    "FIXED_COUNTER_SET_KEYWORDS",
    "FixedCounterPlacementTemplate",
    "FixedCounterPlacementBatchTemplate",
    "FixedCounterPlacementSetTemplate",
    "FixedCounterPlacementTargetSetTemplate",
    "SupportCounterPlacementTemplate",
    "FixedPlayerCounterPlacementTemplate",
    "PlayerCounterPlacementSubject",
    "fixed_counter_placement_effect_template",
    "fixed_counter_placement_batch_effect_template",
    "existing_target_counter_placement_effect_template",
    "fixed_counter_placement_set_effect_template",
    "fixed_counter_placement_target_set_effect_template",
    "support_counter_placement_effect_template",
    "fixed_counter_set_spec_is_closed",
    "fixed_player_counter_placement_effect_template",
]
