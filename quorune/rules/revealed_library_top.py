"""Closed public top-card reveal counts and their ordered results."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from ..object_predicate import ObjectQuerySpec, PermanentStatePredicateSpec
from ..replacement.immutable import FrozenMap, thaw_value
from .committed_quantity_followups import (
    CommittedQuantityFollowup,
    fixed_followup_capabilities,
    quantity_followup_from_count,
)
from .fixed_resolution_characteristic_shapes import fixed_resolution_characteristic_set_node_capabilities
from .fixed_resolution_mana_shapes import fixed_resolution_mana_capabilities


REVEAL_TOP_OPERATION = "reveal_each_library_top"
REVEAL_TOP_MECHANIC = "public-library-top-result"
REVEAL_TOP_CAPABILITY = "library.top.reveal_each_player_result"

_CREATURES = ObjectQuerySpec(zones=("battlefield",), controller="$controller", types_all=("creature",))
_ATTACKERS = ObjectQuerySpec(
    zones=("battlefield",), controller="$controller", types_all=("creature",),
    state_predicate=PermanentStatePredicateSpec(attacking=True),
)
_PUMP = "modify_all_matching_permanents_until_end_of_turn"
_FOLLOWUP_FORMS = {
    (("nonland", "create_token"),),
    (("nonland", "place_counters"),),
    (("nonland", _PUMP),),
    (("land", "create_token"), ("nonland", _PUMP)),
    (("nonland", "mana"), ("nonland", "life")),
}


def _followup_capabilities(effect: FrozenMap) -> tuple[str, ...]:
    operation = effect.get("op")
    if operation == _PUMP:
        literal = thaw_value(effect)
        if (
            set(literal) != {"op", "predicate", "power", "toughness"}
            or literal["predicate"] not in (_CREATURES.to_dict(), _ATTACKERS.to_dict())
            or literal["power"] != 1
            or literal["toughness"] != 1
        ):
            return ()
        return fixed_resolution_characteristic_set_node_capabilities(
            effects=(literal,), target_schema=None, mechanic_ids=("cr-611-continuous-effects",),
        )
    if operation == "mana":
        if set(effect) != {"op", "player", "color", "amount", "source"} or effect["amount"] != 1:
            return ()
        return fixed_resolution_mana_capabilities(
            effects=(effect,), target_schema=None, mechanic_ids=("fixed-resolution-mana",),
        )
    if operation in ("create_token", "place_counters") or (operation == "life" and effect.get("delta") == 1):
        return fixed_followup_capabilities(effect)
    return ()


@dataclass(frozen=True, slots=True)
class RevealedLibraryTopSpec:
    followups: tuple[tuple[str, FrozenMap], ...]

    def __post_init__(self) -> None:
        form = tuple((kind, effect.get("op")) for kind, effect in self.followups)
        if form not in _FOLLOWUP_FORMS or any(
            not _followup_capabilities(effect) for _, effect in self.followups
        ):
            raise ValueError("Public library reveal needs a closed counted instruction sequence")
        if form == (("land", "create_token"), ("nonland", _PUMP)):
            if self.followups[1][1]["predicate"] != FrozenMap(_CREATURES.to_dict()):
                raise ValueError("The two-count reveal must modify the controller's creature set")

    @property
    def capability_dependencies(self) -> tuple[str, ...]:
        return tuple(sorted({
            REVEAL_TOP_CAPABILITY, "zone.draw.library_to_hand",
            *(capability for _, effect in self.followups for capability in _followup_capabilities(effect)),
        }))

    def effect(self) -> dict[str, Any]:
        return {
            "op": REVEAL_TOP_OPERATION,
            "followup": {
                "schema_version": 1,
                "instructions": [{"kind": kind, "effect": thaw_value(effect)} for kind, effect in self.followups],
            },
        }

    @classmethod
    def from_effect(cls, effect: Mapping[str, Any]) -> RevealedLibraryTopSpec:
        if not isinstance(effect, Mapping):
            raise ValueError("Public library reveal descriptor is malformed")
        raw = effect.get("followup")
        if (
            set(effect) != {"op", "followup"}
            or effect["op"] != REVEAL_TOP_OPERATION
            or not isinstance(raw, Mapping)
            or set(raw) != {"schema_version", "instructions"}
            or type(raw["schema_version"]) is not int
            or raw["schema_version"] != 1
            or not isinstance(raw["instructions"], (list, tuple))
            or not 1 <= len(raw["instructions"]) <= 2
        ):
            raise ValueError("Public library reveal descriptor is malformed")
        followups = []
        for instruction in raw["instructions"]:
            if (
                not isinstance(instruction, Mapping)
                or set(instruction) != {"kind", "effect"}
                or instruction["kind"] not in ("land", "nonland")
                or not isinstance(instruction["effect"], Mapping)
                or not isinstance(instruction["effect"].get("op"), str)
            ):
                raise ValueError("Public library reveal instruction is malformed")
            followups.append((instruction["kind"], FrozenMap(instruction["effect"])))
        return cls(tuple(followups))

    def from_revealed_counts(self, *, lands: int, nonlands: int) -> CommittedQuantityFollowup:
        if any(type(count) is not int or count < 0 for count in (lands, nonlands)):
            raise ValueError("Public library reveal counts are malformed")
        counts = {"land": lands, "nonland": nonlands}
        effects = []
        for kind, effect in self.followups:
            count = counts[kind]
            if not count:
                continue
            operation = effect["op"]
            if operation == "mana":
                effects.append(FrozenMap({**effect, "amount": count}))
            elif operation == _PUMP:
                effects.append(FrozenMap({**effect, "power": count, "toughness": count}))
            elif operation == "life":
                effects.append(FrozenMap({**effect, "delta": count, "source": "$source"}))
            else:
                effects.extend(quantity_followup_from_count(effect, count).effects)
        effects.append(FrozenMap({"op": "draw_each_player", "count": 1}))
        return CommittedQuantityFollowup(tuple(effects))


def reveal_top_node_capabilities(*, effects, target_schema, mechanic_ids):
    if target_schema is not None or len(effects) != 1 or REVEAL_TOP_MECHANIC not in mechanic_ids:
        return ()
    try:
        return RevealedLibraryTopSpec.from_effect(effects[0]).capability_dependencies
    except (KeyError, TypeError, ValueError):
        return ()


def is_closed_reveal_top_program(program):
    required = reveal_top_node_capabilities(
        effects=program.effects, target_schema=program.target_schema, mechanic_ids=program.coverage,
    )
    return bool(required) and set(required) <= set(program.capability_dependencies)
