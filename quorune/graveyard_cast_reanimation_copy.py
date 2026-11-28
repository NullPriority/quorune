from __future__ import annotations

"""Closed reanimation followed by an optional copy of its resolving original."""

from collections.abc import Mapping, Sequence

from .replacement.immutable import FrozenMap, thaw_value


GRAVEYARD_REANIMATION_COPY_VERSION = 43
GRAVEYARD_REANIMATION_COPY_CAPABILITY = "stack.copy.resolving_graveyard_reanimation"
RESOLVING_SOURCE_COPY_FIELD = "resolving_source_copy"
GRAVEYARD_REANIMATION_COPY_CLAUSE = "you may copy this spell and may choose a new target for the copy."
_PERMANENT_TYPES = ("artifact", "battle", "creature", "enchantment", "land", "planeswalker")


def is_graveyard_reanimation_copy_target(target_schema) -> bool:
    if not isinstance(target_schema, Mapping):
        return False
    schema = thaw_value(FrozenMap(target_schema))
    maximum = schema.pop("mana_value_max", None)
    return type(maximum) is int and maximum > 0 and schema == {
        "zones": ["graveyard"],
        "categories": ["card"],
        "owner_relation": "you",
        "types_any": list(_PERMANENT_TYPES),
        "count": 1,
    }


def optional_resolving_source_copy(*, controller: str, stack: str) -> dict:
    return {
        "op": "offer_optional_effect",
        "player": controller,
        "effects": [{"op": "copy_stack_item", "stack": stack, RESOLVING_SOURCE_COPY_FIELD: True}],
    }


def validate_graveyard_reanimation_copy_results(
    normal: Mapping, alternate: Sequence[Mapping], controller: str, *, bound: bool,
) -> None:
    move, optional = (thaw_value(FrozenMap(result)) for result in alternate)
    if move != normal or move != {
        "op": "reanimate", "card": move.get("card"), "controller": controller, "tapped": False,
    } or move.get("tapped") is not False:
        raise ValueError("The same original target must return before the optional spell copy")
    card = move["card"]
    if bound:
        if type(card) is not str or not card or card.startswith("$"):
            raise ValueError("Graveyard reanimation requires the bound original target")
    elif card != "$target.0":
        raise ValueError("Graveyard reanimation must return the original target")
    try:
        child, = optional["effects"]
        stack = child["stack"]
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("The optional spell copy must use the resolving original") from exc
    if bound:
        if type(stack) is not str or not stack or stack.startswith("$"):
            raise ValueError("The original spell copy must name the bound resolving spell")
    elif stack != "$stack":
        raise ValueError("The original spell copy must name the resolving spell")
    if optional != optional_resolving_source_copy(controller=controller, stack=stack) or child.get(
        RESOLVING_SOURCE_COPY_FIELD
    ) is not True:
        raise ValueError("The original spell copy must preserve the standard retargeting choice")
