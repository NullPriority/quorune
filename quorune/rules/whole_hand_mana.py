from ..fixed_mana_results import DISCARD_HAND_MANA_SOURCE, WHOLE_HAND_MANA_CAPABILITY as CAPABILITY


HANDLER_ID = "ability.activated.mana.whole-hand-sacrifice.v9"
DISCARD_HAND_COST_FIELD = "discard_hand"
COST = "discard your hand, sacrifice this artifact"
EFFECT = "add three mana of any one color. activate only as an instant."


def is_whole_hand_mana_ability(ability):
    result = ability.fixed_mana_result
    return result is not None and result.kind == DISCARD_HAND_MANA_SOURCE


def whole_hand_mana_text(cost_text, effect_text):
    return (" ".join(cost_text.split()).casefold() == COST
            and " ".join(effect_text.split()).casefold() == EFFECT)
