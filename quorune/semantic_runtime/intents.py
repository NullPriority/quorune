from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Mapping, TypeAlias

from ..affected_permanents import AffectedPermanentSetSpec
from ..public_zone_moves import PublicZoneMoveSetSpec
from ..drawing.model import (
    DiscardDrawnCardUnlessType,
    DrawnCardAction,
    RevealDrawnCard,
)
from ..fixed_damage_set_model import FixedDamageSetSpec
from ..impulse_access_model import ImpulseAccessDuration
from ..entry_counter_model import EffectEntryCounter
from ..creature_subtypes import canonical_creature_subtype
from ..replacement.immutable import FrozenMap, freeze_value
from ..rules.library_scry import ScryArrangement
from ..rules.library_selection import LibrarySelectionArrangement
from ..rules.library_surveillance import SurveilArrangement
from ..zone_object_keyword_model import (
    ZoneObjectKeywordGrantError,
    normalized_zone_object_keyword,
)
from .context import SemanticSourceContext


_EXPLORE_LABEL = "Explore"
_REASON_FIELD = "reason"


def _freeze_replacement_selections(
    values: tuple[str | FrozenMap, ...],
    *,
    family: str,
) -> tuple[str | FrozenMap, ...]:
    frozen: list[str | FrozenMap] = []
    for index, value in enumerate(values):
        if isinstance(value, str):
            if not value:
                raise ValueError(
                    f"{family} replacement selections must be nonempty"
                )
            frozen.append(value)
            continue
        result = freeze_value(
            value,
            field=f"replacement_selections[{index}]",
        )
        if not isinstance(result, FrozenMap):
            raise ValueError(
                f"{family} replacement selections must be objects"
            )
        frozen.append(result)
    return tuple(frozen)


@dataclass(frozen=True, slots=True)
class DrawCardsIntent:
    player: str
    count: int
    reason: str
    private: bool = False
    post_draw_actions: tuple[DrawnCardAction, ...] = ()

    def __post_init__(self) -> None:
        actions = tuple(self.post_draw_actions)
        if any(
            not isinstance(
                action,
                (RevealDrawnCard, DiscardDrawnCardUnlessType),
            )
            for action in actions
        ):
            raise TypeError("Draw intents require typed post-draw actions")
        object.__setattr__(self, "post_draw_actions", actions)


@dataclass(frozen=True, slots=True)
class MillCardsIntent:
    actor: str
    player: str
    count: int
    reason: str

    def __post_init__(self) -> None:
        if any(
            type(value) is not str or not value
            for value in (self.actor, self.player, self.reason)
        ):
            raise ValueError(
                "Mill intents require an actor, player, and reason"
            )
        if type(self.count) is not int or self.count <= 0:
            raise ValueError("Mill intents require a positive fixed count")


@dataclass(frozen=True, slots=True)
class ImpulseAccessIntent:
    actor: str
    player: str
    count: int
    duration: ImpulseAccessDuration
    reason: str

    def __post_init__(self) -> None:
        if any(
            type(value) is not str or not value
            for value in (self.actor, self.player, self.reason)
        ):
            raise ValueError(
                "Impulse-access intents require an actor, player, and reason"
            )
        if type(self.count) is not int or not 1 <= self.count <= 10:
            raise ValueError(
                "Impulse-access intents require a count from one to ten"
            )
        if not isinstance(self.duration, ImpulseAccessDuration):
            raise ValueError(
                "Impulse-access intents require a typed duration"
            )


@dataclass(frozen=True, slots=True)
class BecomeMonarchIntent:
    player: str
    reason: str


@dataclass(frozen=True, slots=True)
class SetPermanentTappedIntent:
    object_ref: str
    actor: str
    tapped: bool
    reason: str
    logical_object_id: str | None = None


@dataclass(frozen=True, slots=True)
class SetPermanentsTappedIntent:
    object_refs: tuple[str, ...]
    actor: str
    tapped: bool
    reason: str

    def __post_init__(self) -> None:
        if (
            not isinstance(self.object_refs, (list, tuple))
            or any(
                type(value) is not str or not value
                for value in self.object_refs
            )
            or len(self.object_refs) != len(set(self.object_refs))
            or type(self.actor) is not str
            or not self.actor
            or type(self.tapped) is not bool
            or type(self.reason) is not str
            or not self.reason
        ):
            raise ValueError("Target-set tap-state intent is malformed")
        object.__setattr__(self, "object_refs", tuple(self.object_refs))


@dataclass(frozen=True, slots=True)
class UntapAllCreaturesIntent:
    actor: str
    reason: str


@dataclass(frozen=True, slots=True)
class CreateRegenerationShieldIntent:
    actor: str
    object_ref: str
    logical_object_id: str | None
    reason: str

    def __post_init__(self) -> None:
        if any(
            type(value) is not str or not value
            for value in (
                self.actor,
                self.object_ref,
                self.reason,
            )
        ) or (
            self.logical_object_id is not None
            and (
                type(self.logical_object_id) is not str
                or not self.logical_object_id
            )
        ):
            raise ValueError(
                "Regeneration intents require actor, object, optional incarnation, and reason"
            )


@dataclass(frozen=True, slots=True)
class DestroyPermanentIntent:
    actor: str
    object_ref: str
    reason: str
    regeneration_prohibited: bool = False
    replacement_selections: tuple[str | FrozenMap, ...] = ()

    def __post_init__(self) -> None:
        if not all((self.actor, self.object_ref, self.reason)):
            raise ValueError(
                "Destruction intents require actor, object, and reason"
            )
        if type(self.regeneration_prohibited) is not bool:
            raise ValueError(
                "Destruction regeneration prohibition must be boolean"
            )
        object.__setattr__(
            self,
            "replacement_selections",
            _freeze_replacement_selections(
                self.replacement_selections,
                family="Destruction",
            ),
        )


@dataclass(frozen=True, slots=True)
class DestroyPermanentSetIntent:
    actor: str
    spec: AffectedPermanentSetSpec
    reason: str
    source_ref: str | None = None
    regeneration_prohibited: bool = False
    replacement_selections: tuple[str | FrozenMap, ...] = ()

    def __post_init__(self) -> None:
        if any(type(value) is not str or not value for value in (self.actor, self.reason)):
            raise ValueError(
                "Destruction-set intents require actor and reason"
            )
        if not isinstance(self.spec, AffectedPermanentSetSpec):
            raise ValueError(
                "Destruction-set intents require a typed affected set"
            )
        if type(self.regeneration_prohibited) is not bool:
            raise ValueError(
                "Destruction-set regeneration prohibition must be boolean"
            )
        if self.source_ref is not None and (
            type(self.source_ref) is not str or not self.source_ref
        ):
            raise ValueError(
                "Destruction-set source must be a nonempty reference"
            )
        if self.spec.exclude_source and self.source_ref is None:
            raise ValueError(
                "Source-excluding destruction sets require a source"
            )
        object.__setattr__(
            self,
            "replacement_selections",
            _freeze_replacement_selections(
                self.replacement_selections,
                family="Destruction-set",
            ),
        )


@dataclass(frozen=True, slots=True)
class DestroyPermanentTargetsIntent:
    actor: str
    object_refs: tuple[str, ...]
    reason: str
    regeneration_prohibited: bool = False
    replacement_selections: tuple[str | FrozenMap, ...] = ()

    def __post_init__(self) -> None:
        if (
            type(self.actor) is not str
            or not self.actor
            or not isinstance(self.object_refs, (list, tuple))
            or any(
                type(value) is not str or not value
                for value in self.object_refs
            )
            or len(self.object_refs) != len(set(self.object_refs))
            or type(self.reason) is not str
            or not self.reason
            or type(self.regeneration_prohibited) is not bool
        ):
            raise ValueError("Target-set destruction intent is malformed")
        object.__setattr__(self, "object_refs", tuple(self.object_refs))
        object.__setattr__(
            self,
            "replacement_selections",
            _freeze_replacement_selections(
                self.replacement_selections,
                family="Target-set destruction",
            ),
        )


@dataclass(frozen=True, slots=True)
class ReturnPermanentToOwnerHandIntent:
    actor: str
    object_ref: str
    reason: str
    replacement_selections: tuple[str | FrozenMap, ...] = ()

    def __post_init__(self) -> None:
        if not all((self.actor, self.object_ref, self.reason)):
            raise ValueError(
                "Return intents require actor, object, and reason"
            )
        object.__setattr__(
            self,
            "replacement_selections",
            _freeze_replacement_selections(
                self.replacement_selections,
                family="return-to-owner-hand",
            ),
        )


@dataclass(frozen=True, slots=True)
class ReturnGraveyardCardToOwnerHandIntent:
    actor: str
    object_ref: str
    reason: str
    replacement_selections: tuple[str | FrozenMap, ...] = ()

    def __post_init__(self) -> None:
        if not all((self.actor, self.object_ref, self.reason)):
            raise ValueError(
                "Graveyard-return intents require actor, object, and reason"
            )
        object.__setattr__(
            self,
            "replacement_selections",
            _freeze_replacement_selections(
                self.replacement_selections,
                family="graveyard-card-return-to-owner-hand",
            ),
        )


@dataclass(frozen=True, slots=True)
class ExilePermanentIntent:
    actor: str
    object_ref: str
    reason: str
    replacement_selections: tuple[str | FrozenMap, ...] = ()

    def __post_init__(self) -> None:
        if not all((self.actor, self.object_ref, self.reason)):
            raise ValueError(
                "Permanent-exile intents require actor, object, and reason"
            )
        object.__setattr__(
            self,
            "replacement_selections",
            _freeze_replacement_selections(
                self.replacement_selections,
                family="Permanent-exile",
            ),
        )


@dataclass(frozen=True, slots=True)
class ExilePublicGraveyardCardIntent:
    actor: str
    object_ref: str
    reason: str
    replacement_selections: tuple[str | FrozenMap, ...] = ()

    def __post_init__(self) -> None:
        if not all((self.actor, self.object_ref, self.reason)):
            raise ValueError(
                "Public-graveyard exile intents require actor, object, and reason"
            )
        object.__setattr__(
            self,
            "replacement_selections",
            _freeze_replacement_selections(
                self.replacement_selections,
                family="Public-graveyard exile",
            ),
        )


@dataclass(frozen=True, slots=True)
class MovePublicZoneSetIntent:
    actor: str
    spec: PublicZoneMoveSetSpec
    reason: str
    source_ref: str | None = None
    replacement_selections: tuple[str | FrozenMap, ...] = ()

    def __post_init__(self) -> None:
        if any(
            type(value) is not str or not value
            for value in (self.actor, self.reason)
        ):
            raise ValueError(
                "Public zone-move set intents require actor and reason"
            )
        if not isinstance(self.spec, PublicZoneMoveSetSpec):
            raise ValueError(
                "Public zone-move set intents require a typed affected set"
            )
        if self.source_ref is not None and (
            type(self.source_ref) is not str or not self.source_ref
        ):
            raise ValueError(
                "Public zone-move source must be a nonempty reference"
            )
        if self.spec.exclude_source and self.source_ref is None:
            raise ValueError(
                "Source-excluding public zone moves require a source"
            )
        object.__setattr__(
            self,
            "replacement_selections",
            _freeze_replacement_selections(
                self.replacement_selections,
                family="Public zone-move set",
            ),
        )


@dataclass(frozen=True, slots=True)
class DealFixedDamageSetIntent:
    actor: str
    source_ref: str
    amount: int
    spec: FixedDamageSetSpec
    reason: str
    replacement_selections: tuple[str | FrozenMap, ...] = ()
    replacement_event_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if any(
            type(value) is not str or not value
            for value in (self.actor, self.source_ref, self.reason)
        ):
            raise ValueError(
                "Fixed damage-set intents require actor, source, and reason"
            )
        if type(self.amount) is not int or self.amount <= 0:
            raise ValueError(
                "Fixed damage-set intent amount must be a positive integer"
            )
        if not isinstance(self.spec, FixedDamageSetSpec):
            raise ValueError(
                "Fixed damage-set intents require a typed recipient set"
            )
        object.__setattr__(
            self,
            "replacement_selections",
            _freeze_replacement_selections(
                self.replacement_selections,
                family="Fixed damage-set",
            ),
        )
        event_ids = tuple(self.replacement_event_ids)
        if any(type(value) is not str or not value for value in event_ids):
            raise ValueError(
                "Fixed damage-set replacement event identities are invalid"
            )
        if len(event_ids) != len(set(event_ids)):
            raise ValueError(
                "Fixed damage-set replacement event identities must be unique"
            )
        object.__setattr__(self, "replacement_event_ids", event_ids)


@dataclass(frozen=True, slots=True)
class AddManaIntent:
    player: str
    color: str
    amount: int
    actor: str
    reason: str
    source_ref: str | None = None


@dataclass(frozen=True, slots=True)
class SetCardDesignationIntent:
    object_ref: str
    designation: Literal["chosen_name", "chosen_creature_type"]
    value: str
    actor: str
    reason: str
    apply_as_subtype: bool = False

    def __post_init__(self) -> None:
        if self.designation not in {
            "chosen_name",
            "chosen_creature_type",
        }:
            raise ValueError("Card designation kind is unsupported")
        if any(
            type(value) is not str or not value
            for value in (
                self.object_ref,
                self.value,
                self.actor,
                self.reason,
            )
        ):
            raise ValueError(
                "Card designation identifiers and text must be nonempty strings"
            )
        if type(self.apply_as_subtype) is not bool:
            raise ValueError("Designation subtype application must be boolean")
        if self.apply_as_subtype and self.designation != "chosen_creature_type":
            raise ValueError(
                "Only a chosen creature type may become a subtype"
            )


@dataclass(frozen=True, slots=True)
class BecomeMonstrousIntent:
    actor: str
    object_id: str
    object_ref: str
    logical_object_id: str
    value: int
    reason: str

    def __post_init__(self) -> None:
        if any(
            type(value) is not str or not value
            for value in (
                self.actor,
                self.object_id,
                self.object_ref,
                self.logical_object_id,
                self.reason,
            )
        ):
            raise ValueError(
                "Monstrous intents require complete object identity"
            )
        if type(self.value) is not int or self.value < 0:
            raise ValueError(
                "Monstrous intents require an exact nonnegative value"
            )


@dataclass(frozen=True, slots=True)
class BecomeRenownedIntent:
    actor: str
    object_id: str
    object_ref: str
    logical_object_id: str
    reason: str

    def __post_init__(self) -> None:
        if any(
            type(value) is not str or not value
            for value in (
                self.actor,
                self.object_id,
                self.object_ref,
                self.logical_object_id,
                self.reason,
            )
        ):
            raise ValueError(
                "Renowned intents require complete object identity"
            )


@dataclass(frozen=True, slots=True)
class RecordChoiceIntent:
    actor: str
    event_code: str
    message: str
    details: FrozenMap
    importance: int = 1
    visibility: tuple[str, ...] | None = None
    changed_object_refs: tuple[str, ...] = ()
    changed_players: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.details, FrozenMap):
            object.__setattr__(self, "details", FrozenMap(self.details))


@dataclass(frozen=True, slots=True)
class ExploreCompletedIntent:
    actor: str
    player: str
    explorer_ref: str
    explorer_logical_object_id: str
    result: Literal[
        "empty_library",
        "land_revealed",
        "nonland_graveyard_choice",
        "nonland_top_choice",
    ]
    reason: str
    revealed_card_ref: str | None = None

    def __post_init__(self) -> None:
        for label, value in (
            ("actor", self.actor),
            ("player", self.player),
            ("explorer_ref", self.explorer_ref),
            ("explorer_logical_object_id", self.explorer_logical_object_id),
            (_REASON_FIELD, self.reason),
        ):
            if type(value) is not str or not value:
                raise ValueError(
                    f"{_EXPLORE_LABEL} {label} must be a nonempty string"
                )
        if self.revealed_card_ref is not None and (
            type(self.revealed_card_ref) is not str
            or not self.revealed_card_ref
        ):
            raise ValueError(
                "Explore revealed-card reference must be nonempty or null"
            )


@dataclass(frozen=True, slots=True)
class ZoneMoveIntent:
    actor: str
    object_ref: str
    expected_zones: tuple[str, ...]
    destination: str
    reason: str
    required_types: tuple[str, ...] = ()
    owned_only: bool = False
    controlled_only: bool = False
    new_controller: str | None = None
    tapped_policy: Literal[
        "preserve", "land_entry", "tapped", "untapped"
    ] = "preserve"
    semantic_events: bool = True
    optional_if_missing: bool = False
    expected_zone_change_counter: int | None = None
    effect_entry_counters: tuple[EffectEntryCounter, ...] = ()
    replacement_selections: tuple[str | FrozenMap, ...] = ()

    def __post_init__(self) -> None:
        if self.expected_zone_change_counter is not None and (
            type(self.expected_zone_change_counter) is not int
            or self.expected_zone_change_counter < 0
        ):
            raise ValueError(
                "Zone move expected zone-change counter must be nonnegative or null"
            )
        counters = tuple(self.effect_entry_counters)
        if any(not isinstance(value, EffectEntryCounter) for value in counters):
            raise ValueError(
                "Zone move effect entry counters must be typed instructions"
            )
        if counters and self.expected_zone_change_counter is None:
            raise ValueError(
                "Effect-generated entry counters require pinned object identity"
            )
        if counters and self.destination != "battlefield":
            raise ValueError(
                "Effect-generated entry counters require a battlefield move"
            )
        object.__setattr__(self, "effect_entry_counters", counters)
        object.__setattr__(
            self,
            "replacement_selections",
            _freeze_replacement_selections(
                tuple(self.replacement_selections),
                family="Zone move",
            ),
        )


@dataclass(frozen=True, slots=True)
class MoveObjectsSimultaneouslyIntent:
    actor: str
    object_refs: tuple[str, ...]
    expected_zones: tuple[str, ...]
    destination: str
    reason: str
    owned_only: bool = False
    controlled_only: bool = False
    replacement_selections: tuple[str | FrozenMap, ...] = ()

    def __post_init__(self) -> None:
        if (
            type(self.actor) is not str
            or not self.actor
            or not isinstance(self.object_refs, (list, tuple))
            or any(type(value) is not str or not value for value in self.object_refs)
            or len(self.object_refs) != len(set(self.object_refs))
            or not isinstance(self.expected_zones, (list, tuple))
            or not self.expected_zones
            or any(
                type(value) is not str or not value
                for value in self.expected_zones
            )
            or len(self.expected_zones) != len(set(self.expected_zones))
            or type(self.destination) is not str
            or not self.destination
            or type(self.reason) is not str
            or not self.reason
            or type(self.owned_only) is not bool
            or type(self.controlled_only) is not bool
        ):
            raise ValueError("Simultaneous object-move intent is malformed")
        object.__setattr__(self, "object_refs", tuple(self.object_refs))
        object.__setattr__(self, "expected_zones", tuple(self.expected_zones))
        object.__setattr__(
            self,
            "replacement_selections",
            _freeze_replacement_selections(
                tuple(self.replacement_selections),
                family="Simultaneous object move",
            ),
        )


@dataclass(frozen=True, slots=True)
class ChooseOneRestBottomRandomIntent:
    actor: str
    player: str
    chosen_ref: str
    looked_refs: tuple[str, ...]
    reason: str
    source_stack_ref: str
    event_code: str = "library.choose_one_rest_bottom_random"


@dataclass(frozen=True, slots=True)
class ShuffleLibraryIntent:
    actor: str
    player: str
    reason: str


@dataclass(frozen=True, slots=True)
class ReturnCardsToLibraryTopIntent:
    actor: str
    player: str
    refs_top_first: tuple[str, ...]
    reason: str


@dataclass(frozen=True, slots=True)
class RecordZoneMoveIntent:
    actor: str
    object_ref: str
    event_code: str
    message: str
    details: FrozenMap
    importance: int = 2
    changed_player: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.details, FrozenMap):
            object.__setattr__(self, "details", FrozenMap(self.details))


@dataclass(frozen=True, slots=True)
class LifeChangeIntent:
    actor: str
    player: str
    amount: int
    reason: str
    source_ref: str | None = None
    replacement_selections: tuple[str | FrozenMap, ...] = ()

    def __post_init__(self) -> None:
        if any(
            type(value) is not str or not value
            for value in (self.actor, self.player, self.reason)
        ):
            raise ValueError(
                "Life-change intents require actor, player, and reason"
            )
        if type(self.amount) is not int:
            raise ValueError("Life-change intent amounts must be integers")
        if self.source_ref is not None and (
            type(self.source_ref) is not str or not self.source_ref
        ):
            raise ValueError(
                "Life-change intent sources must be nonempty references"
            )
        object.__setattr__(
            self,
            "replacement_selections",
            _freeze_replacement_selections(
                tuple(self.replacement_selections),
                family="Life change",
            ),
        )


@dataclass(frozen=True, slots=True)
class PayLifeIntent:
    actor: str
    player: str
    amount: int
    reason: str

    def __post_init__(self) -> None:
        if any(
            type(value) is not str or not value
            for value in (self.actor, self.player, self.reason)
        ):
            raise ValueError(
                "Life-payment intents require actor, player, and reason"
            )
        if type(self.amount) is not int or self.amount < 0:
            raise ValueError(
                "Life-payment intent amounts must be nonnegative integers"
            )


@dataclass(frozen=True, slots=True)
class RevealLibraryCardsIntent:
    actor: str
    player: str
    viewer: str
    refs_top_first: tuple[str, ...]
    reason: str
    public: bool = False


@dataclass(frozen=True, slots=True)
class MoveLibraryCardsToBottomIntent:
    actor: str
    player: str
    refs: tuple[str, ...]
    looked_count: int
    reason: str


@dataclass(frozen=True, slots=True)
class ScryLibraryIntent:
    actor: str
    player: str
    arrangement: ScryArrangement
    reason: str

    def __post_init__(self) -> None:
        if not isinstance(self.arrangement, ScryArrangement):
            raise TypeError("Scry intents require an immutable arrangement")


@dataclass(frozen=True, slots=True)
class SurveilLibraryIntent:
    actor: str
    player: str
    arrangement: SurveilArrangement
    requested_count: int
    reason: str
    replacement_selections: tuple[str | FrozenMap, ...] = ()

    def __post_init__(self) -> None:
        if any(
            type(value) is not str or not value
            for value in (self.actor, self.player, self.reason)
        ):
            raise ValueError(
                "Surveil intents require an actor, player, and reason"
            )
        if not isinstance(self.arrangement, SurveilArrangement):
            raise TypeError("Surveil intents require an immutable arrangement")
        if type(self.requested_count) is not int or self.requested_count <= 0:
            raise ValueError(
                "Surveil intents require a positive fixed count"
            )
        object.__setattr__(
            self,
            "replacement_selections",
            _freeze_replacement_selections(
                tuple(self.replacement_selections),
                family="Surveil",
            ),
        )


@dataclass(frozen=True, slots=True)
class LibrarySelectionIntent:
    actor: str
    player: str
    arrangement: LibrarySelectionArrangement
    reason: str
    source_stack_ref: str
    looked_are_public: bool
    selected_are_public: bool
    replacement_selections: tuple[str | FrozenMap, ...] = ()

    def __post_init__(self) -> None:
        if any(
            type(value) is not str or not value
            for value in (
                self.actor,
                self.player,
                self.reason,
                self.source_stack_ref,
            )
        ):
            raise ValueError(
                "Library selection intents require complete metadata"
            )
        if not isinstance(self.arrangement, LibrarySelectionArrangement):
            raise TypeError(
                "Library selection intents require an immutable arrangement"
            )
        if (
            type(self.looked_are_public) is not bool
            or type(self.selected_are_public) is not bool
        ):
            raise ValueError(
                "Library selection visibility must be a boolean"
            )
        object.__setattr__(
            self,
            "replacement_selections",
            _freeze_replacement_selections(
                tuple(self.replacement_selections),
                family="Library selection",
            ),
        )


@dataclass(frozen=True, slots=True)
class ReorderLibraryTopIntent:
    actor: str
    player: str
    viewer: str
    refs_top_first: tuple[str, ...]
    reason: str


@dataclass(frozen=True, slots=True)
class PayManaCostIntent:
    actor: str
    player: str
    requirements: FrozenMap
    reason: str
    event_code: str
    message: str
    details: FrozenMap
    changed_object_ref: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.requirements, FrozenMap):
            object.__setattr__(
                self,
                "requirements",
                FrozenMap(self.requirements),
            )
        if not isinstance(self.details, FrozenMap):
            object.__setattr__(self, "details", FrozenMap(self.details))


@dataclass(frozen=True, slots=True)
class PlaceCountersIntent:
    actor: str
    object_refs: tuple[str, ...]
    counter_name: str
    amount: int
    reason: str
    source_ref: str | None = None
    replacement_selections: tuple[str | FrozenMap, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "replacement_selections",
            _freeze_replacement_selections(
                tuple(self.replacement_selections),
                family="Counter placement",
            ),
        )


@dataclass(frozen=True, slots=True)
class RemoveCountersIntent:
    actor: str
    object_ref: str
    counter_name: str
    amount: int
    reason: str
    source_ref: str | None = None

    def __post_init__(self) -> None:
        normalized = (
            " ".join(self.counter_name.casefold().split())
            if type(self.counter_name) is str
            else ""
        )
        if any(
            type(value) is not str or not value
            for value in (self.actor, self.object_ref, normalized, self.reason)
        ):
            raise ValueError(
                "Counter-removal intents require actor, object, counter, and reason"
            )
        if type(self.amount) is not int or self.amount <= 0:
            raise ValueError(
                "Counter-removal intent amount must be a positive exact integer"
            )
        if self.source_ref is not None and (
            type(self.source_ref) is not str or not self.source_ref
        ):
            raise ValueError(
                "Counter-removal intent source must be a nonempty reference"
            )
        object.__setattr__(self, "counter_name", normalized)


@dataclass(frozen=True, slots=True)
class RemoveAllCountersIntent:
    actor: str
    object_ref: str
    reason: str
    source_ref: str | None = None

    def __post_init__(self) -> None:
        if any(
            type(value) is not str or not value
            for value in (self.actor, self.object_ref, self.reason)
        ):
            raise ValueError(
                "All-counter removal intents require actor, object, and reason"
            )
        if self.source_ref is not None and (
            type(self.source_ref) is not str or not self.source_ref
        ):
            raise ValueError(
                "All-counter removal intent source must be a nonempty reference"
            )


@dataclass(frozen=True, slots=True)
class CounterPlacementAmount:
    counter_name: str
    amount: int

    def __post_init__(self) -> None:
        normalized = (
            " ".join(self.counter_name.casefold().split())
            if type(self.counter_name) is str
            else ""
        )
        if not normalized or type(self.amount) is not int or self.amount <= 0:
            raise ValueError(
                "Counter batch entries require a name and positive exact amount"
            )
        object.__setattr__(self, "counter_name", normalized)


@dataclass(frozen=True, slots=True)
class PlaceCounterBatchIntent:
    actor: str
    object_ref: str
    placements: tuple[CounterPlacementAmount, ...]
    reason: str
    source_ref: str | None = None
    replacement_selections: tuple[str | FrozenMap, ...] = ()

    def __post_init__(self) -> None:
        if any(
            type(value) is not str or not value
            for value in (self.actor, self.object_ref, self.reason)
        ):
            raise ValueError(
                "Counter batch intents require actor, object, and reason"
            )
        if self.source_ref is not None and (
            type(self.source_ref) is not str or not self.source_ref
        ):
            raise ValueError("Counter batch source must be a nonempty reference")
        placements = tuple(self.placements)
        if not 2 <= len(placements) <= 3 or any(
            not isinstance(value, CounterPlacementAmount)
            for value in placements
        ):
            raise ValueError(
                "Counter batch intents require two or three typed placements"
            )
        names = [value.counter_name for value in placements]
        if len(names) != len(set(names)):
            raise ValueError("Counter batch kinds must be distinct")
        object.__setattr__(self, "placements", placements)
        object.__setattr__(
            self,
            "replacement_selections",
            _freeze_replacement_selections(
                tuple(self.replacement_selections),
                family="Counter batch placement",
            ),
        )


@dataclass(frozen=True, slots=True)
class PlaceCountersOnSetIntent:
    actor: str
    spec: AffectedPermanentSetSpec
    counter_name: str
    amount: int
    reason: str
    source_ref: str | None = None
    replacement_selections: tuple[str | FrozenMap, ...] = ()

    def __post_init__(self) -> None:
        normalized = (
            " ".join(self.counter_name.casefold().split())
            if type(self.counter_name) is str
            else ""
        )
        if any(
            type(value) is not str or not value
            for value in (self.actor, normalized, self.reason)
        ):
            raise ValueError(
                "Counter-set intents require actor, counter, and reason"
            )
        if not isinstance(self.spec, AffectedPermanentSetSpec):
            raise ValueError(
                "Counter-set intents require a typed affected set"
            )
        if type(self.amount) is not int or self.amount <= 0:
            raise ValueError(
                "Counter-set intent amount must be a positive exact integer"
            )
        if self.source_ref is not None and (
            type(self.source_ref) is not str or not self.source_ref
        ):
            raise ValueError(
                "Counter-set intent source must be a nonempty reference"
            )
        if self.spec.exclude_source and self.source_ref is None:
            raise ValueError(
                "Source-excluding counter sets require a source"
            )
        object.__setattr__(self, "counter_name", normalized)
        object.__setattr__(
            self,
            "replacement_selections",
            _freeze_replacement_selections(
                tuple(self.replacement_selections),
                family="Counter-set placement",
            ),
        )


@dataclass(frozen=True, slots=True)
class PlaceCountersOnTargetsIntent:
    actor: str
    object_refs: tuple[str, ...]
    maximum_targets: int
    counter_name: str
    amount: int
    reason: str
    source_ref: str | None = None
    replacement_selections: tuple[str | FrozenMap, ...] = ()

    def __post_init__(self) -> None:
        refs = tuple(self.object_refs)
        normalized = (
            " ".join(self.counter_name.casefold().split())
            if type(self.counter_name) is str
            else ""
        )
        if any(
            type(value) is not str or not value
            for value in (self.actor, normalized, self.reason)
        ):
            raise ValueError(
                "Counter-target intents require actor, counter, and reason"
            )
        if (
            type(self.maximum_targets) is not int
            or self.maximum_targets <= 0
            or len(refs) > self.maximum_targets
            or any(type(ref) is not str or not ref for ref in refs)
            or len(refs) != len(set(refs))
        ):
            raise ValueError(
                "Counter-target intents require unique refs within a positive maximum"
            )
        if type(self.amount) is not int or self.amount <= 0:
            raise ValueError(
                "Counter-target intent amount must be a positive exact integer"
            )
        if self.source_ref is not None and (
            type(self.source_ref) is not str or not self.source_ref
        ):
            raise ValueError(
                "Counter-target intent source must be a nonempty reference"
            )
        object.__setattr__(self, "object_refs", refs)
        object.__setattr__(self, "counter_name", normalized)
        object.__setattr__(
            self,
            "replacement_selections",
            _freeze_replacement_selections(
                tuple(self.replacement_selections),
                family="Counter-target placement",
            ),
        )


@dataclass(frozen=True, slots=True)
class PlacePlayerCountersIntent:
    actor: str
    player_ids: tuple[str, ...]
    counter_name: str
    amount: int
    reason: str
    source_ref: str | None = None
    replacement_selections: tuple[str | FrozenMap, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.player_ids, (list, tuple)):
            raise ValueError(
                "Player counter intent subjects must be an ordered sequence"
            )
        players = tuple(self.player_ids)
        if type(self.counter_name) is not str:
            raise ValueError(
                "Player counter intents require actor, counter, and reason"
            )
        normalized = " ".join(self.counter_name.casefold().split())
        if any(
            type(value) is not str or not value
            for value in (self.actor, normalized, self.reason)
        ):
            raise ValueError(
                "Player counter intents require actor, counter, and reason"
            )
        if (
            not players
            or any(type(player) is not str or not player for player in players)
            or len(players) != len(set(players))
        ):
            raise ValueError(
                "Player counter intent subjects must be unique nonempty seats"
            )
        if type(self.amount) is not int or self.amount <= 0:
            raise ValueError(
                "Player counter intent amount must be a positive integer"
            )
        if self.source_ref is not None and (
            type(self.source_ref) is not str or not self.source_ref
        ):
            raise ValueError(
                "Player counter intent source must be a nonempty reference"
            )
        object.__setattr__(self, "player_ids", players)
        object.__setattr__(self, "counter_name", normalized)
        object.__setattr__(
            self,
            "replacement_selections",
            _freeze_replacement_selections(
                tuple(self.replacement_selections),
                family="Player counter placement",
            ),
        )


@dataclass(frozen=True, slots=True)
class CounterStackIntent:
    actor: str
    stack_ref: str
    reason: str
    countered_by: str


@dataclass(frozen=True, slots=True)
class EliminatePlayersIntent:
    actor: str
    players: tuple[str, ...]
    reason: str


@dataclass(frozen=True, slots=True)
class CopyStackItemIntent:
    actor: str
    controller: str
    target_stack_ref: str
    targets: tuple[str, ...]
    target_groups: FrozenMap
    reason: str

    def __post_init__(self) -> None:
        if not isinstance(self.target_groups, FrozenMap):
            object.__setattr__(
                self,
                "target_groups",
                FrozenMap(self.target_groups),
            )


@dataclass(frozen=True, slots=True)
class RetargetStackItemIntent:
    actor: str
    target_stack_ref: str
    targets: tuple[str, ...]
    target_groups: FrozenMap
    source_stack_ref: str

    def __post_init__(self) -> None:
        if not isinstance(self.target_groups, FrozenMap):
            object.__setattr__(
                self,
                "target_groups",
                FrozenMap(self.target_groups),
            )


@dataclass(frozen=True, slots=True)
class CreateTokenIntent:
    actor: str
    controller: str
    name: str
    quantity: int
    reason: str
    characteristics: FrozenMap = field(default_factory=FrozenMap)
    copy_of: str | None = None
    temporary_keywords: tuple[str, ...] = ()
    sacrifice_at_end_step: bool = False
    sacrifice_on_controller_end_step: bool = False
    replacement_selections: tuple[str | FrozenMap, ...] = ()

    def __post_init__(self) -> None:
        if any(
            type(value) is not str or not value
            for value in (self.actor, self.controller, self.reason)
        ):
            raise ValueError(
                "Token-creation intents require actor, controller, and reason"
            )
        if type(self.quantity) is not int or self.quantity < 0:
            raise ValueError(
                "Token-creation quantity must be an exact nonnegative integer"
            )
        if self.copy_of is not None and (
            type(self.copy_of) is not str or not self.copy_of
        ):
            raise ValueError("Token-copy source must be nonempty or null")
        if type(self.name) is not str or (
            not self.name and self.copy_of is None
        ):
            raise ValueError(
                "Token-creation intents require a name unless copying an object"
            )
        keywords = tuple(self.temporary_keywords)
        if (
            any(type(value) is not str or not value for value in keywords)
            or len(keywords) != len(set(keywords))
        ):
            raise ValueError(
                "Temporary token keywords must be unique nonempty strings"
            )
        if (
            type(self.sacrifice_at_end_step) is not bool
            or type(self.sacrifice_on_controller_end_step) is not bool
        ):
            raise ValueError("Token sacrifice flags must be booleans")
        object.__setattr__(self, "temporary_keywords", keywords)
        if not isinstance(self.characteristics, FrozenMap):
            object.__setattr__(
                self,
                "characteristics",
                FrozenMap(self.characteristics),
            )
        object.__setattr__(
            self,
            "replacement_selections",
            _freeze_replacement_selections(
                tuple(self.replacement_selections),
                family="Token creation",
            ),
        )


@dataclass(frozen=True, slots=True)
class CopyControlledTokensIntent:
    actor: str
    controller: str
    chosen_token_ref: str
    source_stack_ref: str
    reason: str


@dataclass(frozen=True, slots=True)
class AddSubtypeIntent:
    actor: str
    object_ref: str
    subtype: str
    source: SemanticSourceContext
    reason: str

    def __post_init__(self) -> None:
        if any(
            type(value) is not str or not value
            for value in (
                self.actor,
                self.object_ref,
                self.subtype,
                self.reason,
            )
        ):
            raise ValueError(
                "Zone-object subtype intents require actor, object, subtype, and reason"
            )
        if not isinstance(self.source, SemanticSourceContext):
            raise TypeError(
                "Zone-object subtype intents require typed source context"
            )
        subtype = canonical_creature_subtype(self.subtype)
        if subtype is None:
            raise ValueError(
                "Zone-object subtype intents require a pinned creature subtype"
            )
        object.__setattr__(self, "subtype", subtype.title())


@dataclass(frozen=True, slots=True)
class GrantZoneObjectKeywordIntent:
    actor: str
    object_ref: str
    keyword: str
    source: SemanticSourceContext
    reason: str

    def __post_init__(self) -> None:
        if any(
            type(value) is not str or not value
            for value in (
                self.actor,
                self.object_ref,
                self.keyword,
                self.reason,
            )
        ):
            raise ValueError(
                "Zone-object keyword intents require actor, object, keyword, and reason"
            )
        if not isinstance(self.source, SemanticSourceContext):
            raise TypeError(
                "Zone-object keyword intents require typed source context"
            )
        try:
            keyword = normalized_zone_object_keyword(self.keyword)
        except ZoneObjectKeywordGrantError as exc:
            raise ValueError(str(exc)) from exc
        object.__setattr__(self, "keyword", keyword)


@dataclass(frozen=True, slots=True)
class ProliferateSubject:
    subject_kind: Literal["player", "permanent"]
    subject_id: str
    ref: str
    counter_names: tuple[str, ...]
    logical_object_id: str | None = None

    def __post_init__(self) -> None:
        if type(self.subject_kind) is not str or self.subject_kind not in {
            "player",
            "permanent",
        }:
            raise ValueError(
                "Proliferate subjects must be players or permanents"
            )
        if any(
            type(value) is not str or not value
            for value in (self.subject_id, self.ref)
        ):
            raise ValueError(
                "Proliferate subjects require stable IDs and references"
            )
        if not isinstance(self.counter_names, (list, tuple)) or any(
            type(value) is not str or not value
            for value in self.counter_names
        ):
            raise ValueError(
                "Proliferate counter names must be nonempty strings"
            )
        names = tuple(
            " ".join(value.casefold().split())
            for value in self.counter_names
        )
        if (
            not names
            or any(not value for value in names)
            or names != tuple(sorted(set(names)))
        ):
            raise ValueError(
                "Proliferate counter names must be unique canonical strings"
            )
        if self.subject_kind == "player":
            if self.subject_id != self.ref or self.logical_object_id is not None:
                raise ValueError(
                    "Player Proliferate subjects cannot carry object identity"
                )
        elif not self.logical_object_id:
            raise ValueError(
                "Permanent Proliferate subjects require logical identity"
            )
        object.__setattr__(self, "counter_names", names)


@dataclass(frozen=True, slots=True)
class ProliferateIntent:
    actor: str
    subjects: tuple[ProliferateSubject, ...]
    reason: str
    source_ref: str | None = None
    replacement_selections: tuple[str | FrozenMap, ...] = ()

    def __post_init__(self) -> None:
        if any(
            type(value) is not str or not value
            for value in (self.actor, self.reason)
        ):
            raise ValueError(
                "Proliferate intents require an actor and reason"
            )
        if self.source_ref is not None and (
            type(self.source_ref) is not str or not self.source_ref
        ):
            raise ValueError(
                "Proliferate source references must be nonempty or null"
            )
        if any(
            not isinstance(subject, ProliferateSubject)
            for subject in self.subjects
        ):
            raise ValueError(
                "Proliferate intents require typed subject snapshots"
            )
        identities = tuple(
            (subject.subject_kind, subject.subject_id)
            for subject in self.subjects
        )
        if len(identities) != len(set(identities)):
            raise ValueError("Proliferate subject snapshots must be unique")
        object.__setattr__(
            self,
            "replacement_selections",
            _freeze_replacement_selections(
                tuple(self.replacement_selections),
                family="Proliferate",
            ),
        )

    @property
    def selections(self) -> tuple[str, ...]:
        return tuple(subject.ref for subject in self.subjects)


@dataclass(frozen=True, slots=True)
class DomainEffectIntent:
    actor: str
    operation: str
    effect: FrozenMap
    reason: str

    def __post_init__(self) -> None:
        if not isinstance(self.effect, FrozenMap):
            object.__setattr__(self, "effect", FrozenMap(self.effect))


SemanticIntent: TypeAlias = (
    DrawCardsIntent
    | MillCardsIntent
    | BecomeMonarchIntent
    | SetPermanentTappedIntent
    | SetPermanentsTappedIntent
    | UntapAllCreaturesIntent
    | CreateRegenerationShieldIntent
    | DestroyPermanentIntent
    | DestroyPermanentSetIntent
    | DestroyPermanentTargetsIntent
    | ReturnPermanentToOwnerHandIntent
    | ReturnGraveyardCardToOwnerHandIntent
    | ExilePermanentIntent
    | ExilePublicGraveyardCardIntent
    | MovePublicZoneSetIntent
    | DealFixedDamageSetIntent
    | AddManaIntent
    | SetCardDesignationIntent
    | BecomeMonstrousIntent
    | BecomeRenownedIntent
    | RecordChoiceIntent
    | ZoneMoveIntent
    | MoveObjectsSimultaneouslyIntent
    | ChooseOneRestBottomRandomIntent
    | ShuffleLibraryIntent
    | ReturnCardsToLibraryTopIntent
    | RecordZoneMoveIntent
    | LifeChangeIntent
    | PayLifeIntent
    | RevealLibraryCardsIntent
    | MoveLibraryCardsToBottomIntent
    | ScryLibraryIntent
    | SurveilLibraryIntent
    | LibrarySelectionIntent
    | ReorderLibraryTopIntent
    | PayManaCostIntent
    | PlaceCountersIntent
    | RemoveCountersIntent
    | RemoveAllCountersIntent
    | PlaceCounterBatchIntent
    | PlaceCountersOnSetIntent
    | PlaceCountersOnTargetsIntent
    | PlacePlayerCountersIntent
    | CounterStackIntent
    | EliminatePlayersIntent
    | CopyStackItemIntent
    | RetargetStackItemIntent
    | CreateTokenIntent
    | CopyControlledTokensIntent
    | AddSubtypeIntent
    | GrantZoneObjectKeywordIntent
    | ProliferateIntent
    | DomainEffectIntent
)
ResultShape: TypeAlias = Literal["single", "by_player"]


@dataclass(frozen=True, slots=True)
class IntentPlan:
    operation: str
    handler_id: str
    intents: tuple[SemanticIntent, ...]
    result_shape: ResultShape = "single"

    def __post_init__(self) -> None:
        if self.result_shape not in {"single", "by_player"}:
            raise ValueError(f"Unknown intent result shape {self.result_shape!r}")
        if self.result_shape == "single" and len(self.intents) != 1:
            raise ValueError("A single-result plan must contain one intent")
