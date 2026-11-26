from collections.abc import Mapping

from ..fixed_effect_payment import FixedEffectPaymentSpec
from ..object_predicate import ObjectQuerySpec
from ..replacement.immutable import thaw_value


FIELD = "source_entry_discard_exile_version"
CAPABILITY = "effect.choice.discard_creature_or_exile_source"


def creature_hand_payment(actor):
    return FixedEffectPaymentSpec("discard", predicate=ObjectQuerySpec(
        zones=("hand",), owner=actor, types_all=("creature",), known_to_actor=True,
    ))


def decline_exile(card):
    return {"op": "move_if_in_zone", "card": card, "from": "battlefield", "destination": "exile"}


def entry_discard_exile_effect():
    return {"op": "offer_optional_mana_payment", "schema_version": 2, FIELD: 1, "player": "$controller",
            "payment": creature_hand_payment("$controller").to_dict(), "effects": [],
            "declined_effects": [decline_exile("$source.zone_object")]}


def uses_entry_discard_exile(value):
    return isinstance(value, Mapping) and FIELD in value


def validate_entry_discard_exile_resolved(effect, spec, actor):
    if type(effect.get(FIELD)) is not int or effect[FIELD] != 1 or spec != creature_hand_payment(actor) or effect["effects"] not in ([], ()):
        raise ValueError("The source-entry payment requires one private creature-card discard")
    declined = thaw_value(effect.get("declined_effects"))
    if not isinstance(declined, list) or len(declined) != 1 or not isinstance(declined[0], Mapping):
        raise ValueError("The source-entry payment requires its one declined source move")
    card = declined[0].get("card")
    if declined[0] != decline_exile(card) or card is not None and (type(card) is not str or not card or card.startswith("$")):
        raise ValueError("The declined payment may move only its bound original battlefield object")


def entry_discard_exile_capabilities(value):
    if type(value.get(FIELD)) is not int or thaw_value(value) != entry_discard_exile_effect():
        raise ValueError("A compiled entry decline requires its full private cost and original source movement")
    return (CAPABILITY, *creature_hand_payment("$controller").capabilities, "permanent.exile.effect")


def entry_discard_exile_program_dependencies(program, value):
    if (program.event != "permanent.enter.self" or program.active_zone != "battlefield" or program.event_condition is not None
            or program.target_schema is not None or program.cost_schema is not None or program.handlers
            or len(program.effects) != 1 or program.effects[0] != value):
        raise ValueError("Creature-card discard or exile requires its intrinsic source-entry trigger")
    return entry_discard_exile_capabilities(value)
