from collections.abc import Mapping

from ..linked_exile_return_model import validate_linked_ability_binding
from ..replacement.immutable import thaw_value

CAPABILITY = "zone.exile.linked_hand_mana"
CHOICE = "choose_linked_mana_imprint"
PAIR = "linked_hand_mana_pair"
MANA_HANDLER = "ability.activated.mana.linked-hand-colors.v1"
REQUIREMENTS = (CAPABILITY, "zone.exile.linked_ability_pair", "zone.change.destination_replacement",
                "mana.activated.color_set", "trigger.event.normalized_zone_change")
ENTRY = "when this artifact enters, you may exile a nonartifact, nonland card from your hand."
OUTPUT = "add one mana of any of the exiled card's colors."


def normalized(text):
    return " ".join(text.casefold().replace("’", "'").split())


def hand_imprint_effect(pair, *, actor="$controller"):
    validate_linked_ability_binding(pair)
    return {"op": CHOICE, "schema_version": 1, "player": actor, PAIR: pair}


def imprint_pair(effect, *, actor="$controller"):
    if not isinstance(effect, Mapping) or type(effect.get("schema_version")) is not int:
        raise ValueError("Linked hand imprint needs its exact version")
    pair = effect.get(PAIR)
    if thaw_value(effect) != hand_imprint_effect(pair, actor=actor):
        raise ValueError("Linked hand imprint has a closed controller and effect")
    return pair


def imprintable_types(types):
    return not {"artifact", "land"}.intersection(types)
