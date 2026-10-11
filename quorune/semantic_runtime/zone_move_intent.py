from __future__ import annotations

"""Immutable single-object zone moves and replacement selection values."""

from dataclasses import dataclass
from typing import Literal

from ..entry_counter_model import EffectEntryCounter
from ..replacement.immutable import FrozenMap, freeze_value
from ..zone_trigger_events import ZoneTransitionKind


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

    def __post_init__(self) -> None:
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
