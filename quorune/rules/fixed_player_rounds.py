"""The original loss, private discard, creature sacrifice and land sacrifice layout."""

from ..object_predicate import ObjectQuerySpec


FIXED_PLAYER_ROUNDS_TEXT = (
    "Each player loses 1 life, discards a card, sacrifices a creature of their choice, "
    "then sacrifices a land of their choice."
)
PLAYER_ROUND_SLOTS = ("amount", "count", "count", "count")
_FIXED_LIFE_LOSS = {"op": "lose_life", "scope": "all_active_players", "amount": 1}
_ROUND_DOMAINS = (
    ("discard", "hand", ()),
    ("sacrifice", "battlefield", ("creature",)),
    ("sacrifice", "battlefield", ("land",)),
)


def validate_fixed_player_rounds(effects):
    if len(effects) != 4 or effects[0] != _FIXED_LIFE_LOSS:
        raise ValueError("Ordered player rounds start with one all-player life loss")
    for effect, (action, zone, types) in zip(effects[1:], _ROUND_DOMAINS, strict=True):
        keys = {"op", "actor", "players", "zone", "predicate", "count", "then", "prompt"}
        if action == "discard":
            keys.add("hidden")
        predicate = ObjectQuerySpec(zones=(zone,), types_all=types)
        if (set(effect) != keys or effect["op"] != "choose_cards_apnap" or effect["actor"] != "$controller"
                or effect["players"] != "all" or effect["zone"] != zone or effect["then"] != action
                or effect["predicate"] != predicate.to_dict() or type(effect["count"]) is not int
                or effect["count"] != 1 or type(effect["prompt"]) is not str or not effect["prompt"].strip()
                or (action == "discard" and effect["hidden"] is not True)):
            raise ValueError("Ordered player rounds require private discard, creature sacrifice, then land sacrifice")
