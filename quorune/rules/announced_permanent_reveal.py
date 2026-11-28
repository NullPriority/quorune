"""Closed announced-X public reveal and its library/zone transaction dependencies."""

from collections.abc import Mapping

from ..query_effect_amount_model import CastXAmountSpec, DECLARED_AMOUNT_CAPABILITY
from ..replacement.immutable import FrozenMap

OPERATION = "announced_revealed_permanents"
MECHANIC = "announced-revealed-permanent-set"
CAPABILITY = "library.reveal.announced_permanent_set"
PERMANENT_TYPES = frozenset(("artifact", "battle", "creature", "enchantment", "land", "planeswalker"))
DEPENDENCIES = (
    CAPABILITY, DECLARED_AMOUNT_CAPABILITY, "library.select.fixed_controller",
    "zone.change.destination_replacement", "zone.graveyard.ordered_simultaneous_arrival",
    "attachment.aura.simple_object", "attachment.aura.typed_restriction",
)


def announced_permanent_reveal_effect():
    return {"op": OPERATION, "schema_version": 1, "player": "$controller", "amount": CastXAmountSpec().to_dict()}


def validate_announced_permanent_reveal(effect, *, actor=None):
    if not isinstance(effect, Mapping):
        raise ValueError("An announced permanent reveal requires a closed instruction")
    if actor is None:
        if FrozenMap(effect) != FrozenMap(announced_permanent_reveal_effect()):
            raise ValueError("An announced permanent reveal requires the original locked X")
        return
    if (set(effect) != {"op", "schema_version", "player", "amount"} or effect.get("op") != OPERATION
            or type(effect.get("schema_version")) is not int or effect["schema_version"] != 1
            or effect.get("player") != actor or type(effect.get("amount")) is not int or effect["amount"] < 0):
        raise ValueError("An announced permanent reveal requires its original player and resolved nonnegative X")


def announced_permanent_reveal_node_capabilities(*, effects, target_schema, mechanic_ids):
    if target_schema is not None or len(effects) != 1 or MECHANIC not in mechanic_ids:
        return ()
    try:
        validate_announced_permanent_reveal(effects[0])
    except (TypeError, ValueError):
        return ()
    return DEPENDENCIES


def announced_permanent_reveal_payload_dependencies(effect):
    if effect.get("op") != OPERATION:
        return ()
    validate_announced_permanent_reveal(effect)
    return DEPENDENCIES


def is_closed_announced_permanent_reveal_program(program):
    required = announced_permanent_reveal_node_capabilities(
        effects=program.effects, target_schema=program.target_schema, mechanic_ids=program.coverage,
    )
    return ((program.event, program.active_zone) == ("resolve", "stack")
            and bool(required) and set(required).issubset(program.capability_dependencies))
