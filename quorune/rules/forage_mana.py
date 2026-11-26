from collections import Counter
from functools import lru_cache
from itertools import combinations_with_replacement


CAPABILITY = "mana.activated.forage_combination"
HANDLER_ID = "ability.activated.mana.forage-combination.v8"
FORAGE_MANA_COST = "{t}, forage"
FORAGE_MANA_OUTPUT = "add two mana in any combination of colors."


def forage_mana_text(cost_text, effect_text):
    return (" ".join(cost_text.split()).casefold() == FORAGE_MANA_COST
            and " ".join(effect_text.split()).casefold() == FORAGE_MANA_OUTPUT)


def has_forage_mana_choice(ability):
    return any(choice.kind == "forage" for choice in ability.choices)


@lru_cache(maxsize=1)
def forage_mana_modes():
    from ..fixed_mana_abilities import FixedManaMode

    return tuple(FixedManaMode.from_bundle(Counter(pair))
                 for pair in combinations_with_replacement("WUBRG", 2))
