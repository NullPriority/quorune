"""Compile a per-attacker payment offered during a defending player's trigger."""

from . import regex_working_set as re
from ..rules.attacker_mana_payment import DEFENDED_ATTACK_VARIANT, defended_attack_condition
from ..rules.opponent_event_mana_payment import opponent_event_mana_payment_effect
from .optional_payment_templates import FIXED_OPTIONAL_MANA_PAYMENT_MECHANIC
from .token_templates import fixed_token_creation_effect_template


def defended_attacker_binding(intervening):
    from .fixed_counter_trigger_nodes import FixedCounterTriggerBinding
    from .trigger_event_vocabulary import FixedCounterTriggerEvent

    if (intervening["prefix"].casefold() != "whenever a creature an opponent controls attacks"
            or intervening["condition"].casefold() != "you're the defending player"):
        return None
    if _payment(intervening["body"]) is None:
        return None
    return FixedCounterTriggerBinding(
        event=FixedCounterTriggerEvent.PUBLIC_ATTACKS,
        variant=DEFENDED_ATTACK_VARIANT, body=intervening["body"],
        public_condition=defended_attack_condition(), public_active_zone="battlefield",
        public_mechanic="trigger-event-normalized-public-action",
        public_template_id="fixed-counter-public-attack-trigger-v1",
    )


def _payment(body):
    match = re.fullmatch(r"(?P<token>Create .+) unless that creature['’]s controller pays [{](?P<cost>[1-9]|10)[}]\.",
                         body.strip(), re.I)
    if match is None:
        return None
    token = fixed_token_creation_effect_template(match["token"] + ".")
    if token is None:
        return None
    _, children, targets, mechanics = token.compiled()
    if targets is not None or len(children) != 1 or tuple(mechanics) != ("cr-111-tokens",):
        return None
    effect = opponent_event_mana_payment_effect(int(match["cost"]), children[0], attacker=True)
    return "fixed-defended-attacker-mana-payment-v1", (effect,), None, (FIXED_OPTIONAL_MANA_PAYMENT_MECHANIC, *mechanics)


def defended_attacker_payment_result(binding, body):
    if binding.variant == DEFENDED_ATTACK_VARIANT and binding.body == body:
        return _payment(body)
    return None
