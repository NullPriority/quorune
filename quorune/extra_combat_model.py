from __future__ import annotations

"""Closed untap follow-ups and replay state for an additional combat."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Mapping


EXTRA_COMBAT_FOLLOWUP_FIELD = "extra_combat_after_untap"
EXTRA_COMBAT_CAPABILITY = "turn.phase.extra_combat_after_current"
EXTRA_COMBAT_MECHANIC = "fixed-extra-combat-after-untap"
EXTRA_COMBAT_PHASES_FIELD = "extra_combat_phases"


class ExtraCombatUntap(StrEnum):
    SOURCE = "source"
    CONTROLLED_CREATURES = "controlled_creatures"


def extra_combat_effect(untap: ExtraCombatUntap) -> dict[str, Any]:
    if untap is ExtraCombatUntap.SOURCE:
        return {
            "op": "untap",
            EXTRA_COMBAT_FOLLOWUP_FIELD: {"schema_version": 1, "source_card": "$source.zone_object"},
        }
    if untap is ExtraCombatUntap.CONTROLLED_CREATURES:
        return {
            "op": "set_public_tap_state",
            "tapped": False,
            EXTRA_COMBAT_FOLLOWUP_FIELD: {"schema_version": 1, "controlled_creatures": True},
        }
    raise ValueError("Extra combat requires a typed untap scope")


def decode_extra_combat_effect(
    value: Mapping[str, Any], *, resolved: bool = False,
) -> ExtraCombatUntap:
    followup = value.get(EXTRA_COMBAT_FOLLOWUP_FIELD) if isinstance(value, Mapping) else None
    if (not isinstance(followup, Mapping) or type(followup.get("schema_version")) is not int
            or followup["schema_version"] != 1):
        raise ValueError("Extra combat requires its exact untap follow-up")
    operation = value.get("op")
    if operation == "untap":
        if set(value) != {"op", EXTRA_COMBAT_FOLLOWUP_FIELD} or set(followup) != {"schema_version", "source_card"}:
            raise ValueError("Source untap with extra combat has a closed effect shape")
        ref = followup["source_card"]
        if resolved:
            if ref is not None and (type(ref) is not str or not ref):
                raise ValueError("Extra combat requires a resolved source reference or absence")
        elif ref != "$source.zone_object":
            raise ValueError("Extra combat untaps only the original source incarnation")
        return ExtraCombatUntap.SOURCE
    if (
        operation == "set_public_tap_state"
        and set(value) == {"op", "tapped", EXTRA_COMBAT_FOLLOWUP_FIELD}
        and set(followup) == {"schema_version", "controlled_creatures"}
        and followup["controlled_creatures"] is True
        and value["tapped"] is False
    ):
        return ExtraCombatUntap.CONTROLLED_CREATURES
    raise ValueError("Extra combat requires a fixed untap of the controller's creatures")


def extra_combat_effect_capabilities(value: Mapping[str, Any]) -> tuple[str, ...]:
    untap = decode_extra_combat_effect(value)
    tap_capability = (
        "permanent.untap.effect" if untap is ExtraCombatUntap.SOURCE else "permanent.tap_state.fixed_set"
    )
    return EXTRA_COMBAT_CAPABILITY, tap_capability


def extra_combat_node_capabilities(*, effects, target_schema, mechanic_ids) -> tuple[str, ...]:
    if target_schema is not None or len(effects) != 1 or EXTRA_COMBAT_FOLLOWUP_FIELD not in effects[0]:
        return ()
    try:
        return extra_combat_effect_capabilities(effects[0])
    except (TypeError, ValueError):
        return ()


def decode_extra_combat_phases(data: Mapping[str, Any]) -> int:
    if EXTRA_COMBAT_PHASES_FIELD not in data:
        return 0
    value = data[EXTRA_COMBAT_PHASES_FIELD]
    if type(value) is not int or value < 1:
        raise ValueError("Recorded extra combat phases require a positive integer")
    return value


@dataclass(frozen=True, slots=True)
class ScheduleExtraCombatIntent:
    actor: str
    source_ref: str
    reason: str
    untap: ExtraCombatUntap
    permanent_ref: str | None = None
    logical_object_id: str | None = None

    def __post_init__(self) -> None:
        if any(type(value) is not str or not value for value in (self.actor, self.source_ref, self.reason)):
            raise ValueError("Extra combat requires a resolving actor, source and reason")
        if not isinstance(self.untap, ExtraCombatUntap):
            raise ValueError("Extra combat requires a typed untap scope")
        identities = (self.permanent_ref, self.logical_object_id)
        if (self.permanent_ref is None) != (self.logical_object_id is None) or any(
            value is not None and (type(value) is not str or not value) for value in identities
        ):
            raise ValueError("Extra combat requires paired source-incarnation identities")
        if self.permanent_ref is not None and self.untap is not ExtraCombatUntap.SOURCE:
            raise ValueError("Only an extra combat's source untap uses a specific permanent")
