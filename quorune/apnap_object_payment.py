"""Closed timing for an APNAP object payment with a fixed life-loss result."""

from collections.abc import Mapping


APNAP_OBJECT_PAYMENT_CAPABILITY = 'choice.apnap.optional_object_payment_or_life_loss'
TWO_CARD_DISCARD_PAYMENT_CAPABILITY = "choice.apnap.optional_two_card_discard_or_life_loss"
ORDER_GRAVEYARD_ARRIVALS = "order_graveyard_arrivals"
DECLINED_LIFE_LOSS = 'declined_life_loss'


def object_payment_cardinalities(count):
    return (0, count)


def apnap_object_payment_dependencies(effect):
    if not isinstance(effect, Mapping) or DECLINED_LIFE_LOSS not in effect:
        return ()
    amount = effect[DECLINED_LIFE_LOSS]
    payment = (effect.get('zone'), effect.get('then'), effect.get('hidden', False))
    count = effect.get('count')
    if (effect.get('op') != 'choose_cards_apnap'
            or effect.get('players') not in ('all', 'opponents')
            or payment not in (('hand', 'discard', True), ('battlefield', 'sacrifice', False))
            or type(effect.get('hidden', False)) is not bool
            or type(count) is not int or count not in (1, 2)
            or type(amount) is not int or amount <= 0 or not isinstance(effect.get('predicate'), Mapping)
            or {'target', 'exclude_ref', 'fallback_effects', 'require_full_count'} & set(effect)):
        raise ValueError('APNAP optional payment requires a closed object choice and fixed life loss')
    if count == 1:
        return (APNAP_OBJECT_PAYMENT_CAPABILITY,)
    from .object_predicate import ObjectQuerySpec

    if (payment != ("hand", "discard", True) or effect["players"] != "opponents"
            or ObjectQuerySpec.from_dict(effect["predicate"]) != ObjectQuerySpec(zones=("hand",))):
        raise ValueError("A two-card payment requires each opponent's own unqualified hidden hand")
    return APNAP_OBJECT_PAYMENT_CAPABILITY, TWO_CARD_DISCARD_PAYMENT_CAPABILITY


OTHER_PLAYER_SACRIFICE_SCOPE = "other_players"
OTHER_PLAYER_SACRIFICE_CAPABILITY = "choice.affected_player.other_players_sacrifice"
OTHER_PLAYER_SACRIFICE_TYPES = ("creature", "artifact", "enchantment")


def validate_other_player_sacrifice(effect, *, actor):
    from .object_predicate import ObjectQuerySpec

    if (set(effect) != {"op", "actor", "players", "zone", "predicate", "count", "then", "prompt"}
            or effect["op"] != "choose_cards_apnap" or effect["actor"] != actor
            or effect["players"] != OTHER_PLAYER_SACRIFICE_SCOPE or effect["zone"] != "battlefield"
            or type(effect["count"]) is not int or effect["count"] != 1 or effect["then"] != "sacrifice"
            or type(effect["prompt"]) is not str or not effect["prompt"]):
        raise ValueError("Other players must choose their own single untargeted sacrifice")
    predicate = ObjectQuerySpec.from_dict(effect["predicate"])
    if not any(predicate == ObjectQuerySpec(zones=("battlefield",), types_all=(category,))
               for category in OTHER_PLAYER_SACRIFICE_TYPES):
        raise ValueError("Other-player sacrifice requires one fixed displayed permanent type")
