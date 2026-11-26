from __future__ import annotations

"""One untargeted random creature from a resolving own-death instruction."""

from typing import Any, Mapping

from ..announced_damage import ANNOUNCED_DAMAGE_EFFECT_FIELD, AllocatedDamageRecipient
from ..fixed_damage_set import snapshot_fixed_damage_set
from ..fixed_damage_set_model import FixedDamageSetSpec, PermanentControllerRelation, PermanentDamageGroup
from ..object_predicate import ObjectQuerySpec
from .random_selection import sample_identity_rows


RANDOM_OPPONENT_DAMAGE_FIELD = "random_opponent_creature_damage_version"
RANDOM_OPPONENT_DAMAGE_CAPABILITY = "damage.result.random_opponent_creature"
_DEPENDENCIES = (
    RANDOM_OPPONENT_DAMAGE_CAPABILITY,
    "damage.amount.positive",
    "damage.batch.fixed_set",
    "damage.result.multitype_permanent",
    "damage.source.stack_current_or_lki",
)
_OPPONENT_CREATURES = FixedDamageSetSpec((
    PermanentDamageGroup(
        query=ObjectQuerySpec(zones=("battlefield",), types_all=("creature",)),
        controller_relation=PermanentControllerRelation.OPPONENTS,
    ),
))


def random_opponent_damage_instruction(amount: int) -> dict[str, Any]:
    return {"op": "damage_fixed_set", "source": "$source", "amount": amount,
            RANDOM_OPPONENT_DAMAGE_FIELD: 1}


def _validate_instruction(effect: Mapping[str, Any]) -> None:
    amount = effect.get("amount")
    if (
        type(amount) is not int or amount < 1
        or type(effect.get(RANDOM_OPPONENT_DAMAGE_FIELD)) is not int
        or effect != random_opponent_damage_instruction(amount)
    ):
        raise ValueError("Random creature damage requires its complete fixed, untargeted instruction")


def random_opponent_damage_capabilities(effect, target_schema):
    if target_schema is not None:
        return ()
    try:
        _validate_instruction(effect)
    except ValueError:
        return ()
    return _DEPENDENCIES


def _mentions_random_damage(value: Any) -> bool:
    if isinstance(value, Mapping):
        return RANDOM_OPPONENT_DAMAGE_FIELD in value or any(_mentions_random_damage(part) for part in value.values())
    return isinstance(value, (list, tuple)) and any(_mentions_random_damage(part) for part in value)


def random_opponent_damage_runtime_dependencies(program):
    inputs = (program.effects, program.handlers, program.cost_schema, program.target_schema, program.event_condition)
    if not _mentions_random_damage(inputs):
        return ()
    if (
        (program.event, program.active_zone) != ("creature.dies.self", "battlefield")
        or program.target_schema is not None or program.event_condition is not None
        or program.cost_schema is not None or program.handlers or len(program.effects) != 1
    ):
        raise ValueError("Random creature damage requires one complete source-death instruction")
    _validate_instruction(program.effects[0])
    return _DEPENDENCIES


def bind_random_opponent_damage(host, effect, item):
    """Allocate the fixed amount to a selected current creature incarnation."""
    _validate_instruction(effect)
    if item.context.get("event") != "creature.dies" or item.context.get("currently_resolving") is not True:
        raise ValueError("Random creature damage requires its resolving death occurrence")
    snapshot = snapshot_fixed_damage_set(host, actor=item.controller, spec=_OPPONENT_CREATURES)
    selected = sample_identity_rows(
        (recipient.to_dict() for recipient in snapshot.recipients), 1,
        seed=host.state.config.seed, instruction_key=f"{item.ref}|0",
        namespace="opponent-creature-damage", seat=item.controller,
    )
    allocation = [
        AllocatedDamageRecipient(row["ref"], row["logical_object_id"], effect["amount"]).to_dict()
        for row in selected
    ]
    return {
        "op": "damage_fixed_set",
        "source": effect["source"],
        "amount": effect["amount"],
        ANNOUNCED_DAMAGE_EFFECT_FIELD: allocation,
    }
