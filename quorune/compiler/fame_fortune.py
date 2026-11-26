"""Lower the complete untargeted public opponent-choice spell."""

from ..rules.fame_fortune_choices import fame_fortune_effect

_MECHANIC = "public-fame-or-fortune"
_BODY = ("Each opponent chooses fame or fortune. For each player who chose fame, gain control of a creature "
         "that player controls until end of turn. Untap those creatures and they gain haste until end of turn. "
         "For each player who chose fortune, you draw a card and create a Treasure token.")


def fame_fortune_template(text):
    if " ".join(text.split()).casefold() != _BODY.casefold():
        return None
    return "public-fame-or-fortune-v1", (fame_fortune_effect(),), None, (_MECHANIC,)
