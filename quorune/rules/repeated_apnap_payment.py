"""Closed announced-X opponent payment rounds."""

from collections.abc import Mapping

from ..replacement.immutable import FrozenMap
from .effect_shape_walkers import nested_effects


FIELD = "repeated_apnap_payment"
MECHANIC = "announced-x-mixed-apnap-payment"
CAPABILITY = "choice.apnap.repeated_mixed_payment"
REQUIREMENTS = (
    CAPABILITY, "choice.apnap.optional_object_payment_or_life_loss",
    "life.change.effect", "zone.change.destination_replacement",
)


def payment_round_effect(loss):
    if type(loss) is not int or loss <= 0:
        raise ValueError("An APNAP payment round requires positive life loss")
    return {"op": "choose_cards_apnap", FIELD: 1, "declined_life_loss": loss}


def is_payment_round(effect):
    return isinstance(effect, Mapping) and effect.get("op") == "choose_cards_apnap" and FIELD in effect


def payment_round_node_capabilities(effects, target_schema, cost_schema, mechanics):
    if MECHANIC not in mechanics and not any(is_payment_round(effect) for effect in effects):
        return None
    if target_schema is not None or cost_schema is not None or len(effects) != 1:
        return ()
    effect = effects[0]
    try:
        if type(effect.get(FIELD)) is int and FrozenMap(effect) == FrozenMap(payment_round_effect(effect.get("declined_life_loss"))):
            return REQUIREMENTS
    except ValueError:
        pass
    return ()


def payment_round_program_dependencies(program):
    uses = any(is_payment_round(effect) for effect in nested_effects((program.effects, program.handlers)))
    if not uses and MECHANIC not in program.coverage:
        return ()
    owned = payment_round_node_capabilities(program.effects, program.target_schema, program.cost_schema, program.coverage)
    if (not owned or MECHANIC not in program.coverage or program.handlers or program.event_condition is not None
            or not program.ability_id.startswith("spell:") or (program.event, program.active_zone) != ("resolve", "stack")):
        raise ValueError("Repeated payment must be the original complete untargeted spell")
    return owned
