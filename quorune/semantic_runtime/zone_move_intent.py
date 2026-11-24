from __future__ import annotations

"""Immutable zone-movement intents and replacement selection values."""

from dataclasses import dataclass
from typing import Literal, Mapping

from ..entry_counter_model import EffectEntryCounter
from ..replacement.immutable import FrozenMap, freeze_value
from ..zone_trigger_events import ZoneTransitionKind
from ..continuous_effect_model import ContinuousObjectIdentity
from ..public_zone_moves import PublicZoneMoveSetSpec, PUBLIC_AURA_ENTRY_VERSIONS
from ..rules.public_exile_result_model import PublicExileFollowup


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

    def __post_init__(self) -> None:
        if type(self.manifest_entry) is not bool or (self.manifest_entry and (
                self.expected_zones != ('library',) or self.destination != 'battlefield'
                or self.expected_zone_change_counter is None or self.new_controller != self.actor)):
            raise ValueError('Manifest zone intent requires one controller-bound library incarnation')
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
    spec: PublicZoneMoveSetSpec
    reason: str
    source_ref: str | None = None
    replacement_selections: tuple[str | FrozenMap, ...] = ()
    followup: PublicExileFollowup | None = None
    aura_target_refs: FrozenMap | None = None

    def __post_init__(self) -> None:
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
        if not isinstance(self.spec, PublicZoneMoveSetSpec):
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
