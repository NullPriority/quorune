"""One battlefield entry chosen from the resolving spell's committed Mill."""

from collections.abc import Mapping
from dataclasses import dataclass

from ..replacement.immutable import FrozenMap
from .committed_mill_cards import PUBLIC_MILLED_CARDS_CAPABILITY
from .committed_quantity_followups import CommittedQuantityFollowup
from .effect_shape_walkers import nested_effects


MILL_PERMANENT_ENTRY_KIND = "choose_one_permanent_to_battlefield"
MILL_PERMANENT_ENTRY_CAPABILITY = "zone.mill.committed_permanent_battlefield_choice"
MILL_PERMANENT_CHOICE = "committed_mill_battlefield_choice"
MILL_PERMANENT_COUNT = 7
_ELIGIBLE_TYPES = frozenset({"creature", "enchantment", "land"})


def eligible_milled_permanent(types):
    return bool(_ELIGIBLE_TYPES.intersection(types))


@dataclass(frozen=True, slots=True)
class MillOnePermanentToBattlefield:
    """The fixed Mill instruction and its mandatory single-card result."""

    def to_dict(self):
        return {"schema_version": 1, "kind": MILL_PERMANENT_ENTRY_KIND}

    @classmethod
    def from_dict(cls, value):
        result = cls()
        if (not isinstance(value, Mapping) or type(value.get("schema_version")) is not int
                or dict(value) != result.to_dict()):
            raise ValueError("Committed Mill permanent result is malformed")
        return result

    def effect(self):
        return {"op": "mill", "player": "$controller", "count": MILL_PERMANENT_COUNT,
                "followup": self.to_dict()}

    @property
    def dependencies(self):
        return ("zone.mill.fixed", PUBLIC_MILLED_CARDS_CAPABILITY, MILL_PERMANENT_ENTRY_CAPABILITY,
                "zone.change.destination_replacement", "attachment.aura.typed_restriction")

    def from_committed_result(self, host, result):
        from ..milling import current_public_milled_cards

        candidates = []
        for card in current_public_milled_cards(host, result):
            types, _, _ = host._type_parts(str(host._effective_card_data(card).get("type_line") or ""))
            if eligible_milled_permanent(types):
                candidates.append(MilledPermanent(card.ref, card.object_id, card.logical_object_id,
                                                 card.zone, card.zone_change_counter).to_dict())
        if not candidates:
            return CommittedQuantityFollowup(())
        return CommittedQuantityFollowup((FrozenMap({
            "op": "choose_option", "player": result.player,
            MILL_PERMANENT_CHOICE: {"schema_version": 1, "candidates": tuple(candidates)},
        }),))


@dataclass(frozen=True, slots=True)
class MilledPermanent:
    ref: str
    object_id: str
    logical_object_id: str
    zone: str
    zone_change_counter: int

    def __post_init__(self):
        from ..zone_transition_model import PUBLIC_ZONES

        if (any(type(value) is not str or not value for value in
                (self.ref, self.object_id, self.logical_object_id, self.zone))
                or self.zone not in PUBLIC_ZONES or type(self.zone_change_counter) is not int
                or self.zone_change_counter < 0
                or self.logical_object_id != f"{self.object_id}@{self.zone_change_counter}"):
            raise ValueError("Committed Mill candidate lacks its exact public incarnation")

    def to_dict(self):
        return {"ref": self.ref, "object_id": self.object_id, "logical_object_id": self.logical_object_id,
                "zone": self.zone, "zone_change_counter": self.zone_change_counter}

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, Mapping) or set(value) != {
            "ref", "object_id", "logical_object_id", "zone", "zone_change_counter",
        }:
            raise ValueError("Committed Mill candidate identity fields are closed")
        return cls(**value)


def mill_battlefield_choice_candidates(effect, *, actor):
    if (not isinstance(effect, Mapping) or set(effect) != {"op", "player", MILL_PERMANENT_CHOICE}
            or effect["op"] != "choose_option" or effect["player"] != actor
            or type(actor) is not str or not actor):
        raise ValueError("Committed Mill selection requires one resolving controller")
    value = effect[MILL_PERMANENT_CHOICE]
    if (not isinstance(value, Mapping) or set(value) != {"schema_version", "candidates"}
            or type(value["schema_version"]) is not int or value["schema_version"] != 1
            or not isinstance(value["candidates"], (tuple, list))):
        raise ValueError("Committed Mill selection has a closed versioned batch")
    candidates = tuple(MilledPermanent.from_dict(row) for row in value["candidates"])
    if (not candidates or len({row.ref for row in candidates}) != len(candidates)
            or len({row.object_id for row in candidates}) != len(candidates)):
        raise ValueError("Committed Mill selection needs a distinct nonempty public result")
    return candidates


def uses_milled_permanent_battlefield(effect):
    followup = effect.get("followup")
    return (effect.get("op") == "mill" and isinstance(followup, Mapping)
            and followup.get("kind") == MILL_PERMANENT_ENTRY_KIND)


def milled_permanent_battlefield_node_capabilities(effects, target_schema):
    if len(effects) != 1 or target_schema is not None or not uses_milled_permanent_battlefield(effects[0]):
        return ()
    effect, = effects
    try:
        result = MillOnePermanentToBattlefield.from_dict(effect["followup"])
    except (TypeError, ValueError):
        return ()
    if type(effect.get("count")) is not int or dict(effect) != result.effect():
        return ()
    return result.dependencies


def milled_permanent_battlefield_program_dependencies(program):
    marked = tuple(effect for effect in nested_effects((program.effects, program.handlers, program.target_schema))
                   if uses_milled_permanent_battlefield(effect) or MILL_PERMANENT_CHOICE in effect)
    if not marked and MILL_PERMANENT_ENTRY_CAPABILITY not in program.capability_dependencies:
        return ()
    required = milled_permanent_battlefield_node_capabilities(program.effects, program.target_schema)
    if (not required or marked != tuple(program.effects) or program.handlers or program.cost_schema is not None
            or program.event_condition is not None or (program.event, program.active_zone) != ("resolve", "stack")
            or not program.ability_id.startswith("spell:")):
        raise ValueError("Committed Mill battlefield choice requires its complete resolving spell")
    return required


def is_closed_milled_permanent_battlefield_program(program):
    try:
        required = milled_permanent_battlefield_program_dependencies(program)
    except ValueError:
        return False
    return bool(required) and set(required).issubset(program.capability_dependencies)
