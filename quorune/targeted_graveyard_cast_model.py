from __future__ import annotations

"""Closed instruction and continuation data for one resolving graveyard cast."""

from collections.abc import Mapping
from dataclasses import dataclass
import hashlib

from .replacement.immutable import FrozenMap, freeze_value, thaw_value
from .util import stable_json

GRAVEYARD_CAST_OPERATION = "cast_targeted_graveyard_spell"
GRAVEYARD_CAST_CAPABILITY = "casting.resolution.targeted_graveyard"
GRAVEYARD_CAST_MECHANIC = "targeted-resolving-graveyard-cast"
GRAVEYARD_CAST_FIELDS = frozenset({"op", "player", "card", "owner_relation", "exile_on_graveyard"})


def validate_graveyard_cast_effect(effect, *, resolved=False):
    if (not isinstance(effect, Mapping) or set(effect) != GRAVEYARD_CAST_FIELDS
            or effect["op"] != GRAVEYARD_CAST_OPERATION or effect["owner_relation"] not in ("any", "opponent", "you")
            or type(effect["exile_on_graveyard"]) is not bool
            or effect["owner_relation"] == "you" and not effect["exile_on_graveyard"]):
        raise ValueError("The resolving graveyard cast instruction changed")
    if resolved:
        if any(type(effect[key]) is not str or not effect[key] for key in ("player", "card")):
            raise ValueError("The graveyard cast requires a bound controller and card")
    elif effect["player"] != "$controller" or effect["card"] != "$target.0":
        raise ValueError("The graveyard cast requires the declared target and resolving controller")


def graveyard_cast_options_fingerprint(options):
    return hashlib.sha256(stable_json([thaw_value(option) for option in options]).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class GraveyardCastIntent:
    stack_ref: str
    effect: FrozenMap
    logical_object_id: str
    options_fingerprint: str
    response: FrozenMap
    replacement_selections: tuple[str | FrozenMap, ...] = ()

    def __post_init__(self):
        validate_graveyard_cast_effect(self.effect, resolved=True)
        if not isinstance(self.effect, FrozenMap):
            object.__setattr__(self, "effect", FrozenMap(self.effect))
        if isinstance(self.response, Mapping) and not isinstance(self.response, FrozenMap):
            object.__setattr__(self, "response", FrozenMap(self.response))
        if (any(type(value) is not str or not value for value in
                (self.stack_ref, self.logical_object_id, self.options_fingerprint))
                or not isinstance(self.effect, FrozenMap) or not isinstance(self.response, FrozenMap)
                or any(key.startswith("_") for key in self.response)):
            raise ValueError("The resolving graveyard cast intent is malformed")
        selections = freeze_value(self.replacement_selections, field="resolving graveyard cast replacements")
        if not isinstance(selections, tuple) or any(
            not isinstance(selection, FrozenMap) and (type(selection) is not str or not selection)
            for selection in selections
        ):
            raise ValueError("The resolving cast requires canonical replacement selections")
        object.__setattr__(self, "replacement_selections", selections)

    def identity(self):
        return {"stack_ref": self.stack_ref, "effect": thaw_value(self.effect),
                "logical_object_id": self.logical_object_id, "options_fingerprint": self.options_fingerprint,
                "response": thaw_value(self.response)}


def decode_graveyard_cast_identity(value):
    if (not isinstance(value, Mapping)
            or set(value) != {"stack_ref", "effect", "logical_object_id", "options_fingerprint", "response"}
            or not all(isinstance(value[key], Mapping) for key in ("effect", "response"))):
        raise ValueError("The graveyard cast continuation identity is malformed")
    return GraveyardCastIntent(
        value["stack_ref"], FrozenMap(value["effect"]), value["logical_object_id"],
        value["options_fingerprint"], FrozenMap(value["response"]),
    ).identity()
