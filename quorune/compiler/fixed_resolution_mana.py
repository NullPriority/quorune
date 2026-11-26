from __future__ import annotations

"""Closed fixed symbol bundles through the existing resolution mana owner."""

from collections import Counter
from . import regex_working_set as re
from ..abilities import _mana_spend_restriction
from ..mana_spend_rules import NONCAST_MANA_PAYMENT_RESTRICTION

FIXED_RESOLUTION_MANA_MECHANIC = 'fixed-resolution-mana'
FIXED_RESOLUTION_MANA_CAPABILITY = 'mana.production.fixed_resolution'


def fixed_resolution_mana_effect_template(text: str):
    from ..rules.turn_retained_resolution_mana import PRINTED_EFFECT, effect

    if " ".join(text.replace("’", "'").split()).casefold() == PRINTED_EFFECT:
        return "fixed-turn-retained-red-mana-v1", (effect(),), None, (FIXED_RESOLUTION_MANA_MECHANIC,)
    choice=re.fullmatch(r'Add (?P<amount>one|two|three|four|five|six|seven|eight|nine|ten|[1-9][0-9]*) mana (?P<selection>of any (?:one )?color|in any combination of colors)\.?',text.strip(),re.I)
    if choice is not None:
        from .fixed_numbers import fixed_number
        from ..resolution_mana_model import ResolutionManaChoiceSpec
        amount=fixed_number(choice['amount'])
        if amount is None or not 1<=amount<=10:return None
        spec=ResolutionManaChoiceSpec('combination' if choice['selection'].casefold().startswith('in ') else 'one_color',amount)
        return 'fixed-resolution-mana-choice-v2',(spec.effect(),),None,(FIXED_RESOLUTION_MANA_MECHANIC,)
    restriction = None
    noncast = re.fullmatch(r"(?P<body>Add (?:\{[WUBRGC]\})+)\. This mana can['’]t be spent to cast spells\.",
                           text.strip(), re.I)
    if noncast is not None:
        text = noncast['body']
        restriction = NONCAST_MANA_PAYMENT_RESTRICTION
    elif '. Spend this mana only to ' in text:
        body, _tail = text.split('. Spend this mana only to ', 1)
        restriction = _mana_spend_restriction(text)
        if restriction is None:
            return None
        text = body + '.'
    match = re.fullmatch(r'Add (?P<symbols>(?:\{[WUBRGC]\})+)\.?', text.strip(), re.I)
    if match is None:
        return None
    symbols = re.findall(r'\{([WUBRGC])\}', match['symbols'].upper())
    if not 1 <= len(symbols) <= 10:
        return None
    counts = Counter(symbols)
    effects = tuple(
        {
            'op': 'mana', 'player': '$controller', 'color': color,
            'amount': counts[color], 'source': '$source',
            **({'spend_restriction': restriction} if restriction else {}),
        }
        for color in 'WUBRGC' if counts[color]
    )
    return 'fixed-resolution-mana-bundle-v1', effects, None, (FIXED_RESOLUTION_MANA_MECHANIC,)


def source_attack_turn_retained_mana_result(binding, body):
    from ..replacement.immutable import thaw_value
    from ..rules.turn_retained_resolution_mana import ATTACK_EVENT_CONDITION, ATTACK_PRINTED_EFFECT, attack_effects

    if ((binding.event.value, binding.variant, binding.active_zone) != ("creature.attacks", "source_attacks", "battlefield")
            or thaw_value(binding.event_condition) != ATTACK_EVENT_CONDITION or body != binding.body
            or " ".join(body.replace("’", "'").split()).casefold() != ATTACK_PRINTED_EFFECT):
        return None
    return ("fixed-source-attack-turn-retained-mana-v1", attack_effects(), None, (FIXED_RESOLUTION_MANA_MECHANIC,))
