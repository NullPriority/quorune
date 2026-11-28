from __future__ import annotations

"""Owned artifact recursion with a ceiling from the actual departing permanent."""

from .graveyard_card_targets import GraveyardCardTargetKind, OwnGraveyardCardTargetSpec
from ..replacement.immutable import thaw_value

CAPABILITY = "target.mana_value.artifact_graveyard_departure"
MECHANIC = "artifact-graveyard-departure-return"
EVENT = "permanent.graveyard"
VARIANT = "source_or_controlled_artifact_graveyard"


def departure_artifact_return_condition():
    same_source = {"field": "card", "op": "eq", "value": "$source.ref"}
    controlled_artifact = {
        "all": [
            {"field": "card", "op": "ne", "value": "$source.ref"},
            {"field": "controller", "op": "eq", "value": "$source.controller"},
            {"field": "types", "op": "contains_any", "value": ["artifact"]},
        ],
    }
    return {"any": [same_source, controlled_artifact]}


def departure_artifact_return_result():
    target = OwnGraveyardCardTargetSpec(GraveyardCardTargetKind.ARTIFACT_CARD).to_target_schema()
    effect = {"op": "return_graveyard_card_to_owner_hand", "card": "$target.0"}
    return "artifact-graveyard-departure-return-v1", (effect,), target, (
        "return-to-owner-hand", "cr-115-targets", MECHANIC,
    )


def departure_artifact_return_runtime_dependencies(program):
    if (CAPABILITY not in program.capability_dependencies and MECHANIC not in program.coverage):
        return ()
    _, effects, target, _ = departure_artifact_return_result()
    if (
        program.event != EVENT or program.active_zone != "battlefield"
        or program.handlers or program.cost_schema is not None
        or thaw_value(program.event_condition) != departure_artifact_return_condition()
        or tuple(thaw_value(effect) for effect in program.effects) != effects
        or thaw_value(program.target_schema) != target
        or MECHANIC not in program.coverage
    ):
        raise ValueError("The departure return requires one exact owned artifact target and event")
    return (CAPABILITY,)


def bind_departure_artifact_return_target(program, context):
    """Project the sealed death value into the same target schema used at resolution."""
    if not departure_artifact_return_runtime_dependencies(program):
        return {}
    amount = context.get("mana_value")
    if (
        context.get("from") != "battlefield" or context.get("to") != "graveyard"
        or not isinstance(context.get("card_object_identity"), str)
        or not context["card_object_identity"]
        or type(amount) not in (int, float) or amount < 0
        or (type(amount) is float and not amount.is_integer())
    ):
        raise ValueError("The departure return requires an individual committed battlefield mana value")
    target = {**thaw_value(program.target_schema), "mana_value_max": int(amount) - 1}
    return {"target_schema_override": target}
