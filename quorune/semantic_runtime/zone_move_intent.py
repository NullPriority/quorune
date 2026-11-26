from __future__ import annotations

"""Immutable zone-movement intents and replacement selection values."""

from dataclasses import dataclass
from typing import Literal, Mapping

from ..entry_counter_model import EffectEntryCounter
from ..replacement.immutable import FrozenMap, freeze_value
from ..zone_trigger_events import ZoneTransitionKind
from ..continuous_effect_model import ContinuousObjectIdentity
from ..public_zone_moves import FixedMixedZoneExileSpec, PublicZoneMoveSetSpec, PUBLIC_AURA_ENTRY_VERSIONS
from ..rules.public_exile_result_model import PublicExileFollowup
from ..fractional_quantity_model import HalfQuantitySpec
from ..query_effect_amount_model import OpponentHandMillCount
from ..selected_graveyard_mill_count import SelectedGraveyardMillCount
from ..rules.milled_creature_tokens import MilledCreatureTokens
from ..rad_counters import RadMillFollowup

from ..discard_effect_origin import DiscardEffectOrigin
from ..hand_library_redraw_model import HandLibraryArrivalOrder
from ..library_order_model import DepartedLibraryArrivalOrder, LibraryArrivalOrder
from ..public_zone_moves import PublicBattlefieldController


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
class MoveObjectsSimultaneouslyIntent:
    actor: str
    object_refs: tuple[str, ...]
    expected_zones: tuple[str, ...]
    destination: str
    reason: str
    transition_kind: ZoneTransitionKind = ZoneTransitionKind.ORDINARY
    owned_only: bool = False
    controlled_only: bool = False
    replacement_selections: tuple[str | FrozenMap, ...] = ()
    battlefield_controller: PublicBattlefieldController | None = None
    battlefield_tapped: bool = False
    library_order: LibraryArrivalOrder | None = None
    discard_effect_origin: DiscardEffectOrigin | None = None
    entry_life_payment_refs: tuple[str, ...] = ()

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
            or not isinstance(self.transition_kind, ZoneTransitionKind)
            or type(self.owned_only) is not bool
            or type(self.controlled_only) is not bool
            or type(self.battlefield_tapped) is not bool
            or self.battlefield_controller is not None and (
                not isinstance(self.battlefield_controller,PublicBattlefieldController) or self.destination!='battlefield')
            or self.battlefield_controller is None and self.battlefield_tapped
        ):
            raise ValueError("Simultaneous object-move intent is malformed")
        if (not isinstance(self.entry_life_payment_refs, tuple)
                or any(type(ref) is not str or ref not in self.object_refs for ref in self.entry_life_payment_refs)
                or len(set(self.entry_life_payment_refs)) != len(self.entry_life_payment_refs)
                or self.entry_life_payment_refs and self.battlefield_controller is not PublicBattlefieldController.ACTOR):
            raise ValueError("Batch entry life payments require distinct selected entries under the actor")
        if self.discard_effect_origin is not None and (
            not isinstance(self.discard_effect_origin, DiscardEffectOrigin)
            or self.transition_kind is not ZoneTransitionKind.DISCARD
            or tuple(self.expected_zones) != ("hand",) or self.destination != "graveyard"
        ):
            raise ValueError("A resolving discard origin belongs only to a requested hand discard")
        if self.library_order is not None and (
            not isinstance(self.library_order, LibraryArrivalOrder)
            or not isinstance(self.library_order, (DepartedLibraryArrivalOrder, HandLibraryArrivalOrder)) and self.library_order.chooser != self.actor
            or self.destination != "library"
            or tuple(self.expected_zones) != self.library_order.origin_zones
            or self.transition_kind is not ZoneTransitionKind.ORDINARY
            or self.controlled_only
            or set(self.library_order.members) != set(self.object_refs)
        ):
            raise ValueError("Ordered library movement requires the exact graveyard batch")
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
class FrontFaceGraveyardCreatureReturnIntent(MoveObjectsSimultaneouslyIntent):
    """Return the selected owned creature cards using their legal printed fronts."""

    def __post_init__(self) -> None:
        MoveObjectsSimultaneouslyIntent.__post_init__(self)
        if (
            self.expected_zones != ("graveyard",) or self.destination != "battlefield"
            or self.battlefield_controller is not PublicBattlefieldController.ACTOR
            or not self.owned_only or self.controlled_only or self.battlefield_tapped
            or self.transition_kind is not ZoneTransitionKind.ORDINARY or self.entry_life_payment_refs
        ):
            raise ValueError("Front-face return requires an ordinary owned graveyard creature batch")


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
    transition_kind: ZoneTransitionKind = ZoneTransitionKind.ORDINARY
    manifest_entry: bool = False
    cloak_entry: bool = False

    @property
    def library_face_down_entry(self) -> bool:
        return self.manifest_entry or self.cloak_entry

    def __post_init__(self) -> None:
        if (
            type(self.manifest_entry) is not bool or type(self.cloak_entry) is not bool
            or self.manifest_entry and self.cloak_entry
            or self.library_face_down_entry and (
                self.expected_zones != ('library',) or self.destination != 'battlefield'
                or self.expected_zone_change_counter is None or self.new_controller != self.actor
            )
        ):
            raise ValueError('A face-down zone intent requires one controller-bound library incarnation')
        if not isinstance(self.transition_kind, ZoneTransitionKind):
            raise ValueError("Zone move transition kind must be typed")
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
class ExilePublicGraveyardCardIntent:
    actor: str
    object_ref: str
    reason: str
    replacement_selections: tuple[str | FrozenMap, ...] = ()
    linked_source: ContinuousObjectIdentity | None = None
    linked_pair: str | None = None

    def __post_init__(self) -> None:
        if self.linked_source is not None or self.linked_pair is not None:
            from ..rules.linked_clue_copy import validate_linked_clue_pair

            if not isinstance(self.linked_source, ContinuousObjectIdentity):
                raise ValueError("Linked graveyard exile requires the original source incarnation")
            from ..linked_exile_return_model import LINKED_ABILITY_BINDING_PREFIX
            if isinstance(self.linked_pair, str) and self.linked_pair.startswith(LINKED_ABILITY_BINDING_PREFIX):
                from ..linked_exile_activation_model import validate_grant_pair

                validate_grant_pair(self.linked_pair)
            else:
                validate_linked_clue_pair(self.linked_pair)
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
    spec: PublicZoneMoveSetSpec | FixedMixedZoneExileSpec
    reason: str
    source_ref: str | None = None
    replacement_selections: tuple[str | FrozenMap, ...] = ()
    followup: PublicExileFollowup | None = None
    aura_target_refs: FrozenMap | None = None

    def __post_init__(self) -> None:
        if isinstance(self.spec, FixedMixedZoneExileSpec) and (
            self.followup is not None or self.aura_target_refs is not None
        ):
            raise ValueError("Mixed-zone exile permits only its original simultaneous move")
        if self.followup is not None:
            if not isinstance(self.followup, PublicExileFollowup):
                raise ValueError("Public exile follow-up must be typed")
            self.followup.validate_move(self.spec)
        if any(
            type(value) is not str or not value
            for value in (self.actor, self.reason)
        ):
            raise ValueError(
                "Public zone-move set intents require actor and reason"
            )
        if not isinstance(self.spec, (PublicZoneMoveSetSpec, FixedMixedZoneExileSpec)):
            raise ValueError(
                "Public zone-move set intents require a typed affected set"
            )
        if self.aura_target_refs is not None:
            if (self.spec.schema_version not in PUBLIC_AURA_ENTRY_VERSIONS or self.spec.destination.value != "battlefield"
                    or not isinstance(self.aura_target_refs, Mapping)
                    or any(type(key) is not str or not key or type(value) is not str or not value
                           for key, value in self.aura_target_refs.items())):
                raise ValueError("Public Aura entry selections require a battlefield set and nonempty identities")
            object.__setattr__(self, "aura_target_refs", FrozenMap(self.aura_target_refs))
        if self.source_ref is not None and (
            type(self.source_ref) is not str or not self.source_ref
        ):
            raise ValueError(
                "Public zone-move source must be a nonempty reference"
            )
        if isinstance(self.spec, PublicZoneMoveSetSpec) and self.spec.exclude_source and self.source_ref is None:
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
class MillCardsIntent:
    actor: str
    player: str
    count: int | HalfQuantitySpec | OpponentHandMillCount | SelectedGraveyardMillCount
    reason: str
    batch_players: tuple[str, ...] = ()
    replacement_selections: tuple[str | FrozenMap, ...] = ()
    followup: MilledCreatureTokens | RadMillFollowup | None = None

    def __post_init__(self) -> None:
        if any(
            type(value) is not str or not value
            for value in (self.actor, self.player, self.reason)
        ):
            raise ValueError(
                "Mill intents require an actor, player, and reason"
            )
        if not isinstance(self.count, (HalfQuantitySpec, OpponentHandMillCount, SelectedGraveyardMillCount)) and (
                type(self.count) is not int or self.count < 0):
            raise ValueError("Mill intents require a nonnegative count or a closed quantity")
        if not isinstance(self.batch_players, (list, tuple)):
            raise ValueError("Mill batch recipients require a typed sequence")
        players = tuple(self.batch_players)
        if (
            any(type(player) is not str or not player for player in players)
            or len(set(players)) != len(players)
            or players and self.player != self.actor
        ):
            raise ValueError("Mill batch recipients must be distinct typed players")
        if isinstance(self.count, OpponentHandMillCount) and (not players or self.actor in players):
            raise ValueError("Hand-count Mill intents require an opponent batch")
        if isinstance(self.count, SelectedGraveyardMillCount) and not players:
            raise ValueError("Graveyard-count Mill intents require the selected player batch")
        if isinstance(self.followup, RadMillFollowup):
            if players or self.actor != self.player or type(self.count) is not int or self.count <= 0:
                raise ValueError("Radiation requires resolved single-player Mill on its controller")
        elif self.followup is not None and (
            not isinstance(self.followup, MilledCreatureTokens) or type(self.count) is not int or self.actor not in players
        ):
            raise ValueError("Committed creature tokens require ordinary all-player Mill with a resolved count")
        object.__setattr__(self, "batch_players", players)
        object.__setattr__(self, "replacement_selections", _freeze_replacement_selections(
            self.replacement_selections, family="Mill",
        ))
