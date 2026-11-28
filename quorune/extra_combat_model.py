from __future__ import annotations

"""Closed untap follow-ups and replay state for an additional combat."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Mapping


EXTRA_COMBAT_FOLLOWUP_FIELD = "extra_combat_after_untap"
EXTRA_COMBAT_CAPABILITY = "turn.phase.extra_combat_after_current"
EXTRA_COMBAT_OTHER_CREATURES_CAPABILITY = "turn.phase.extra_combat_after_other_creature_untap"
EXTRA_COMBAT_MECHANIC = "fixed-extra-combat-after-untap"
EXTRA_COMBAT_PHASES_FIELD = "extra_combat_phases"
PAID_ATTACKING_COMBAT_CAPABILITY = "turn.phase.extra_combat_after_paid_attacker_untap"
CONTROLLED_UNTAP_COMBAT_CAPABILITY = "turn.phase.combat_and_main_after_controlled_creature_untap"
CONTROLLED_UNTAP_COMBAT_MECHANIC = "fixed-combat-and-main-after-controlled-creature-untap"
ATTACKING_CREATURES_VERSION = 4
CONTROLLED_CREATURES_MAIN_PAIR_VERSION = 5
INSERTED_MAIN_COUNT_FIELD = "inserted_main_phase_count"
LATER_MAIN_PHASE_FIELD = "later_than_second_main_phase"


class ExtraCombatUntap(StrEnum):
    SOURCE = "source"
    CONTROLLED_CREATURES = "controlled_creatures"
    GRANTED_COMBAT_CREATURES = "granted_combat_creatures"
    OTHER_CONTROLLED_CREATURES = "other_controlled_creatures"
    CONTROLLED_CREATURES_IF_OWN_COMBAT = "controlled_creatures_if_own_combat"
    ATTACKING_CREATURES = "attacking_creatures"
    CONTROLLED_CREATURES_MAIN_PAIR = "controlled_creatures_main_pair"


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
    if untap is ExtraCombatUntap.GRANTED_COMBAT_CREATURES:
        return {
            "op": "set_public_tap_state", "tapped": False,
            EXTRA_COMBAT_FOLLOWUP_FIELD: {"schema_version": 2, "untap_at_granted_combat": True},
        }
    if untap is ExtraCombatUntap.OTHER_CONTROLLED_CREATURES:
        return {
            "op": "set_public_tap_state", "tapped": False,
            EXTRA_COMBAT_FOLLOWUP_FIELD: {"schema_version": 2, "exclude_source_card": "$source.zone_object"},
        }
    if untap is ExtraCombatUntap.CONTROLLED_CREATURES_IF_OWN_COMBAT:
        return {
            "op": "set_public_tap_state", "tapped": False,
            EXTRA_COMBAT_FOLLOWUP_FIELD: {"schema_version": 3, "own_combat_only": True},
        }
    if untap is ExtraCombatUntap.ATTACKING_CREATURES:
        return {
            "op": "set_public_tap_state", "tapped": False,
            EXTRA_COMBAT_FOLLOWUP_FIELD: {"schema_version": ATTACKING_CREATURES_VERSION, "attacking_creatures": True},
        }
    if untap is ExtraCombatUntap.CONTROLLED_CREATURES_MAIN_PAIR:
        return {
            "op": "set_public_tap_state", "tapped": False,
            EXTRA_COMBAT_FOLLOWUP_FIELD: {"schema_version": CONTROLLED_CREATURES_MAIN_PAIR_VERSION, "controlled_creatures_main_pair": True},
        }
    raise ValueError("Extra combat requires a typed untap scope")


def _uses_combat_untap_result(value, version, field):
    followup = value.get(EXTRA_COMBAT_FOLLOWUP_FIELD) if isinstance(value, Mapping) else None
    return isinstance(followup, Mapping) and (followup.get("schema_version") == version or field in followup)


def uses_paid_attacking_combat(value):
    return _uses_combat_untap_result(value, ATTACKING_CREATURES_VERSION, "attacking_creatures")


def uses_controlled_main_combat(value):
    return _uses_combat_untap_result(value, CONTROLLED_CREATURES_MAIN_PAIR_VERSION, "controlled_creatures_main_pair")


def _decode_immediate_combat_untap(value, followup, mode, field):
    if (type(followup.get("schema_version")) is not int or followup.get(field) is not True
            or value.get("tapped") is not False or dict(value) != extra_combat_effect(mode)):
        raise ValueError("Extra combat requires its exact immediate untap and phase scope")
    return mode


def decode_extra_combat_effect(
    value: Mapping[str, Any], *, resolved: bool = False,
) -> ExtraCombatUntap:
    followup = value.get(EXTRA_COMBAT_FOLLOWUP_FIELD) if isinstance(value, Mapping) else None
    if uses_paid_attacking_combat(value):
        return _decode_immediate_combat_untap(value, followup, ExtraCombatUntap.ATTACKING_CREATURES, "attacking_creatures")
    if uses_controlled_main_combat(value):
        return _decode_immediate_combat_untap(value, followup, ExtraCombatUntap.CONTROLLED_CREATURES_MAIN_PAIR,
                                              "controlled_creatures_main_pair")
    if (isinstance(followup, Mapping) and type(followup.get("schema_version")) is int
            and followup.get("schema_version") == 3 and followup.get("own_combat_only") is True
            and value.get("tapped") is False
            and dict(value) == extra_combat_effect(ExtraCombatUntap.CONTROLLED_CREATURES_IF_OWN_COMBAT)):
        return ExtraCombatUntap.CONTROLLED_CREATURES_IF_OWN_COMBAT
    if (not isinstance(followup, Mapping) or type(followup.get("schema_version")) is not int
            or followup["schema_version"] not in (1, 2)):
        raise ValueError("Extra combat requires its exact untap follow-up")
    if followup["schema_version"] == 2 and "untap_at_granted_combat" in followup:
        from .replacement.immutable import FrozenMap

        if (FrozenMap(value) != FrozenMap(extra_combat_effect(ExtraCombatUntap.GRANTED_COMBAT_CREATURES))
                or followup["untap_at_granted_combat"] is not True or value["tapped"] is not False):
            raise ValueError("The granted combat untap needs its closed delayed result")
        return ExtraCombatUntap.GRANTED_COMBAT_CREATURES
    operation = value.get("op")
    if followup["schema_version"] == 2:
        if (operation != "set_public_tap_state" or set(value) != {"op", "tapped", EXTRA_COMBAT_FOLLOWUP_FIELD}
                or value["tapped"] is not False or set(followup) != {"schema_version", "exclude_source_card"}):
            raise ValueError("Untapping other creatures requires its exact source exclusion")
        ref = followup["exclude_source_card"]
        if resolved:
            if ref is not None and (type(ref) is not str or not ref or ref.startswith("$")):
                raise ValueError("Extra combat requires a resolved source exclusion or absence")
        elif ref != "$source.zone_object":
            raise ValueError("Extra combat excludes only the original source incarnation")
        return ExtraCombatUntap.OTHER_CONTROLLED_CREATURES
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
    if untap is ExtraCombatUntap.GRANTED_COMBAT_CREATURES:
        return EXTRA_COMBAT_CAPABILITY, MAIN_PHASE_EXTRA_COMBAT_CAPABILITY, tap_capability
    if untap is ExtraCombatUntap.ATTACKING_CREATURES:
        return EXTRA_COMBAT_CAPABILITY, tap_capability, PAID_ATTACKING_COMBAT_CAPABILITY, "state_query.permanent.public_state_predicate"
    if untap is ExtraCombatUntap.CONTROLLED_CREATURES_MAIN_PAIR:
        return EXTRA_COMBAT_CAPABILITY, tap_capability, CONTROLLED_UNTAP_COMBAT_CAPABILITY
    additional = ((EXTRA_COMBAT_OTHER_CREATURES_CAPABILITY,)
                  if untap is ExtraCombatUntap.OTHER_CONTROLLED_CREATURES else ())
    return EXTRA_COMBAT_CAPABILITY, tap_capability, *additional


def extra_combat_node_capabilities(*, effects, target_schema, mechanic_ids) -> tuple[str, ...]:
    if target_schema is not None or len(effects) != 1 or EXTRA_COMBAT_FOLLOWUP_FIELD not in effects[0]:
        return ()
    try:
        return extra_combat_effect_capabilities(effects[0])
    except (TypeError, ValueError):
        return ()


def uses_other_creature_extra_combat(effect):
    followup = effect.get(EXTRA_COMBAT_FOLLOWUP_FIELD)
    return isinstance(followup, Mapping) and (
        "exclude_source_card" in followup
        or (followup.get("schema_version") == 2 and "untap_at_granted_combat" not in followup)
    )


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
    grant_stack_ref: str | None = None
    grant_source_object_id: str | None = None
    grant_source_logical_id: str | None = None

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
        if self.permanent_ref is not None and self.untap not in {ExtraCombatUntap.SOURCE, ExtraCombatUntap.OTHER_CONTROLLED_CREATURES}:
            raise ValueError("Only an extra combat's source untap uses a specific permanent")
        grant_source = (self.grant_source_object_id, self.grant_source_logical_id)
        if self.untap in {ExtraCombatUntap.GRANTED_COMBAT_CREATURES, ExtraCombatUntap.CONTROLLED_CREATURES_MAIN_PAIR}:
            if (type(self.grant_stack_ref) is not str or not self.grant_stack_ref
                    or (grant_source[0] is None) != (grant_source[1] is None)
                    or any(value is not None and (type(value) is not str or not value) for value in grant_source)):
                raise ValueError("Main-phase combat requires its original resolving grant and source provenance")
        elif self.grant_stack_ref is not None or any(value is not None for value in grant_source):
            raise ValueError("Historical extra combats cannot carry a main-phase grant")

MAIN_PHASE_COMBATS_FIELD = "main_phase_extra_combats"
MAIN_PHASE_COMBAT_GRANT_FIELD = "main_phase_combat_grant"
MAIN_PHASE_EXTRA_COMBAT_CAPABILITY = "turn.phase.extra_combat_after_own_main"
MAIN_PHASE_EXTRA_COMBAT_MECHANIC = "own-main-landfall-extra-combat"
MAIN_PHASE_EXTRA_COMBAT_CONDITION_FIELD = "own_main_phase_for_combat"
MAIN_PHASES = frozenset({"precombat_main", "postcombat_main"})


@dataclass(frozen=True, slots=True)
class MainPhaseInsertion:
    kind: str
    grant: str | None = None

    def __post_init__(self):
        if self.kind == "combat" and type(self.grant) is str and self.grant:
            return
        if self.kind in {"beginning", "main"} and self.grant is None:
            return
        raise ValueError("A main-phase insertion needs a beginning phase, added main or exact combat grant")

    def to_dict(self):
        return {"kind": self.kind, "grant": self.grant}

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, Mapping) or set(value) != {"kind", "grant"}:
            raise ValueError("Main-phase insertion fields are closed")
        return cls(value["kind"], value["grant"])


@dataclass(frozen=True, slots=True)
class MainPhaseCombatSchedule:
    after_phase: str
    turn_sequence: int
    pending: tuple[MainPhaseInsertion, ...]
    engaged: bool = False
    combat_grant: str | None = None
    reached_added_main: bool = False

    def __post_init__(self):
        if (
            self.after_phase not in MAIN_PHASES or type(self.turn_sequence) is not int or self.turn_sequence < 0
            or type(self.pending) is not tuple or any(not isinstance(value, MainPhaseInsertion) for value in self.pending)
            or type(self.engaged) is not bool or (not self.engaged and not self.pending)
            or self.combat_grant is not None and (not self.engaged or type(self.combat_grant) is not str or not self.combat_grant)
            or type(self.reached_added_main) is not bool or (self.reached_added_main and not self.engaged)
            or self.after_phase != "postcombat_main" and not self.reached_added_main
               and any(value.kind == "beginning" for value in self.pending)
        ):
            raise ValueError("A main-phase combat schedule has an exact turn and phase anchor")
        initial = "combat" if self.engaged else None
        for previous, current in zip((initial, *(entry.kind for entry in self.pending)), self.pending):
            if current.kind == "main" and previous != "combat":
                raise ValueError("An added main must follow its additional combat")
        grants = [value.grant for value in self.pending if value.kind == "combat"]
        if len(grants) != len(set(grants)) or self.combat_grant in grants:
            raise ValueError("A main-phase combat grant must occur exactly once")

    def to_dict(self):
        return {
            "schema_version": 1, "after_phase": self.after_phase, "turn_sequence": self.turn_sequence,
            "pending": [value.to_dict() for value in self.pending],
            "engaged": self.engaged, "combat_grant": self.combat_grant,
            **({"reached_added_main": True} if self.reached_added_main else {}),
        }

    @classmethod
    def from_dict(cls, value):
        fields = {"schema_version", "after_phase", "turn_sequence", "pending", "engaged", "combat_grant"}
        reached = isinstance(value, Mapping) and "reached_added_main" in value
        if reached:
            fields.add("reached_added_main")
        if (
            not isinstance(value, Mapping) or set(value) != fields
            or type(value["schema_version"]) is not int or value["schema_version"] != 1
            or type(value["pending"]) is not list
            or (reached and value["reached_added_main"] is not True)
        ):
            raise ValueError("Main-phase combat schedule fields are closed")
        return cls(
            value["after_phase"], value["turn_sequence"],
            tuple(MainPhaseInsertion.from_dict(entry) for entry in value["pending"]),
            value["engaged"], value["combat_grant"], reached,
        )


def decode_main_phase_combat_schedule(data):
    if MAIN_PHASE_COMBATS_FIELD not in data:
        return None
    schedule = MainPhaseCombatSchedule.from_dict(data[MAIN_PHASE_COMBATS_FIELD])
    if schedule.turn_sequence != data.get("turn_sequence"):
        raise ValueError("Main-phase combat schedule must belong to the recorded current turn")
    return schedule


def decode_inserted_main_phase_count(data):
    if INSERTED_MAIN_COUNT_FIELD not in data:
        return 0
    count = data[INSERTED_MAIN_COUNT_FIELD]
    if type(count) is not int or count < 1:
        raise ValueError("Recorded additional main phases require a positive integer")
    return count


def later_main_phase_context(state, phase, step):
    if (phase, step) != ("postcombat_main", "main"):
        return {}
    queue = state.main_phase_extra_combats
    after_precombat = queue is not None and queue.after_phase == "precombat_main" and queue.reached_added_main
    return {LATER_MAIN_PHASE_FIELD: True} if state.inserted_main_phase_count > int(after_precombat) else {}


def main_phase_extra_combat_condition():
    return {"all": [
        {"field": "controller", "op": "eq", "value": "$source.controller"},
        {"field": MAIN_PHASE_EXTRA_COMBAT_CONDITION_FIELD, "op": "eq", "value": True},
    ]}


def own_main_phase_for_combat(state, controller):
    return (
        type(controller) is str and state.active_player == controller and state.current_turn is not None
        and state.phase in MAIN_PHASES and state.step == "main"
    )
