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
GRAVEYARD_CAST_MAXIMUM = "mana_value_maximum"
EQUIPPED_PLAYER_COMBAT_DAMAGE = FrozenMap({"all": [
    {"field": "source_attachment_target_ref", "op": "eq", "value": "$context.source"},
    {"field": "target_kind", "op": "eq", "value": "player"},
    {"field": "combat", "op": "truthy", "value": True},
]})


def surveil_graveyard_cast_effects():
    return (
        {"op": "surveil", "player": "$controller", "count": 2},
        {"op": GRAVEYARD_CAST_OPERATION, "player": "$controller", "owner_relation": "you",
         "exile_on_graveyard": True, GRAVEYARD_CAST_MAXIMUM: 2},
    )


def is_bounded_graveyard_cast(effect):
    return isinstance(effect, Mapping) and GRAVEYARD_CAST_MAXIMUM in effect


def _validate_bounded_graveyard_cast(effect, *, resolved):
    expected = surveil_graveyard_cast_effects()[1]
    if resolved:
        player = effect.get("player")
        if type(player) is not str or not player or player.startswith("$"):
            raise ValueError("The bounded graveyard cast requires its resolving player")
        expected["player"] = player
        if "card" in effect:
            card = effect["card"]
            if type(card) is not str or not card or card.startswith("$"):
                raise ValueError("The bounded graveyard cast requires its current selection")
            expected["card"] = card
    if (effect != expected or type(effect.get(GRAVEYARD_CAST_MAXIMUM)) is not int
            or effect.get("exile_on_graveyard") is not True):
        raise ValueError("The bounded graveyard cast instruction changed")
FIRST_PAID_GRAVEYARD_CAST_FIELD = "first_paid_cast_version"
FIRST_PAID_GRAVEYARD_CAST_CAPABILITY = "casting.resolution.targeted_graveyard_first_paid"


def first_paid_graveyard_cast(effect):
    return FIRST_PAID_GRAVEYARD_CAST_FIELD in effect


def first_paid_graveyard_cast_effect():
    return {"op": GRAVEYARD_CAST_OPERATION, "player": "$controller", "card": "$target.0",
            "owner_relation": "you", "exile_on_graveyard": False, FIRST_PAID_GRAVEYARD_CAST_FIELD: 1}


def validate_graveyard_cast_effect(effect, *, resolved=False):
    if is_bounded_graveyard_cast(effect):
        _validate_bounded_graveyard_cast(effect, resolved=resolved)
        return
    if not isinstance(effect, Mapping):
        raise ValueError("The resolving graveyard cast instruction must be a mapping")
    paid = first_paid_graveyard_cast(effect)
    fields = GRAVEYARD_CAST_FIELDS | {FIRST_PAID_GRAVEYARD_CAST_FIELD} if paid else GRAVEYARD_CAST_FIELDS
    if (set(effect) != fields or effect["op"] != GRAVEYARD_CAST_OPERATION
            or effect["owner_relation"] not in ("any", "opponent", "you")
            or type(effect["exile_on_graveyard"]) is not bool):
        raise ValueError("The resolving graveyard cast instruction changed")
    if paid:
        version = effect[FIRST_PAID_GRAVEYARD_CAST_FIELD]
        if type(version) is not int or version != 1 or effect["owner_relation"] != "you" or effect["exile_on_graveyard"]:
            raise ValueError("The first paid graveyard cast requires the exact owned-card permission")
    elif effect["owner_relation"] == "you" and not effect["exile_on_graveyard"]:
        raise ValueError("The free owned graveyard cast requires its exile rider")
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
        if self.effect.get("card") is None:
            raise ValueError("The resolving graveyard cast intent requires its selected card")
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
