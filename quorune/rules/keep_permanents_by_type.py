"""Public choices for the original four printed types before one sacrifice batch."""

from collections.abc import Mapping
from dataclasses import dataclass

from ..replacement.immutable import FrozenMap
from .effect_shape_walkers import nested_effects


KEEP_PERMANENTS_BY_TYPE_OPERATION = "choose_permanents_to_keep_by_type"
KEEP_PERMANENTS_BY_TYPE_CAPABILITY = "choice.affected_player.keep_by_permanent_type"
KEEP_PERMANENTS_BY_TYPE_TEMPLATE = "keep-permanents-by-type-then-sacrifice-v1"
_KEEP_TYPES = ("artifact", "creature", "enchantment")


@dataclass(frozen=True, slots=True)
class KeepPermanentsByType:
    mode: str

    def __post_init__(self):
        if type(self.mode) is not str or self.mode not in {"nonland_self_choice", "nonland_controller_choice", "land_self_choice"}:
            raise ValueError("Keep-by-type sacrifice requires one exact printed chooser and domain")

    @property
    def types(self):
        fourth = "land" if self.mode == "land_self_choice" else "planeswalker"
        return (*_KEEP_TYPES, fourth)

    def chooser(self, controller, affected):
        return controller if self.mode == "nonland_controller_choice" else affected

    def can_keep(self, types):
        return self.mode != "nonland_self_choice" or "land" not in types

    def can_sacrifice(self, types):
        return self.mode == "land_self_choice" or "land" not in types

    def effect(self):
        return {
            "op": KEEP_PERMANENTS_BY_TYPE_OPERATION, "schema_version": 1, "mode": self.mode,
            "player": "$controller",
        }

    @classmethod
    def from_effect(cls, value):
        if (not isinstance(value, Mapping) or set(value) != {"op", "schema_version", "mode", "player"}
                or value.get("op") != KEEP_PERMANENTS_BY_TYPE_OPERATION
                or value.get("player") != "$controller"
                or type(value.get("schema_version")) is not int or value["schema_version"] != 1):
            raise ValueError("Keep-by-type sacrifice needs its complete untouched printed descriptor")
        return cls(value["mode"])


def keep_by_type_capabilities(effect, target_schema):
    if target_schema is not None:
        raise ValueError("Keeping a permanent is a public choice without targets")
    scope = KeepPermanentsByType.from_effect(effect)
    return (KEEP_PERMANENTS_BY_TYPE_CAPABILITY, "choice.affected_player.fixed_sacrifice",
            "zone.change.destination_replacement",
            *(("trigger.event.normalized_zone_change", "trigger.placement.apnap")
              if scope.mode == "nonland_self_choice" else ()))


def keep_by_type_runtime_dependencies(program):
    marked = tuple(effect for effect in nested_effects((*program.effects, *program.handlers))
                   if effect.get("op") == KEEP_PERMANENTS_BY_TYPE_OPERATION)
    if not marked and KEEP_PERMANENTS_BY_TYPE_CAPABILITY not in program.capability_dependencies:
        return ()
    if len(marked) != 1 or tuple(program.effects) != marked or program.handlers or program.cost_schema is not None:
        raise ValueError("Keep-by-type sacrifice requires one original complete result")
    scope = KeepPermanentsByType.from_effect(marked[0])
    original = ("permanent.enter.self", "battlefield") if scope.mode == "nonland_self_choice" else ("resolve", "stack")
    if (program.event, program.active_zone) != original or program.event_condition is not None:
        raise ValueError("Keep-by-type sacrifice requires its printed self entry or resolving spell")
    return keep_by_type_capabilities(marked[0], program.target_schema)


def keep_by_type_plan(scope, query, order):
    if (not isinstance(order, (tuple, list)) or len(order) != len(set(order))
            or any(type(seat) is not str or not seat for seat in order)
            or set(order) != set(query.active_seats)):
        raise ValueError("Keep-by-type sacrifice requires the exact active APNAP order")
    slots = []
    sacrifices = []
    identities = {}
    for seat in order:
        rows = sorted(query.objects(zones=("battlefield",), controller=seat), key=lambda row: row.ref)
        current = tuple(row for row in rows if not row.phased_out)
        for row in current:
            if row.ref in identities or not row.logical_object_id or row.controller != seat:
                raise ValueError("Keep-by-type sacrifice lost an original public permanent")
            identities[row.ref] = row.logical_object_id
            if scope.can_sacrifice(row.types):
                sacrifices.append(row.ref)
        for category in scope.types:
            options = [row.ref for row in current if category in row.types and scope.can_keep(row.types)]
            if options:
                slots.append({"affected": seat, "category": category, "options": options})
    return FrozenMap({"slots": slots, "sacrifices": sacrifices, "identities": identities})


def validate_keep_by_type_choices(plan, chosen):
    slots = plan["slots"]
    if (not isinstance(chosen, (list, tuple)) or len(chosen) > len(slots)
            or any(type(ref) is not str or ref not in slot["options"] for ref, slot in zip(chosen, slots))):
        raise ValueError("Keep-by-type sacrifice must preserve its exact choices in slot order")


def unkept_type_choice_refs(plan, chosen):
    validate_keep_by_type_choices(plan, chosen)
    if len(chosen) != len(plan["slots"]):
        raise ValueError("All available type choices must precede the sacrifice")
    kept = set(chosen)
    return tuple(ref for ref in plan["sacrifices"] if ref not in kept)
