"""Fixed optional object payments use the existing APNAP choice owner."""

from . import regex_working_set as re

from ..apnap_object_payment import DECLINED_LIFE_LOSS
from .affected_player_discard_templates import fixed_affected_player_discard_effect_template
from .affected_player_sacrifice_templates import fixed_affected_player_sacrifice_effect_template


_INSTRUCTION = re.compile(
    r'(?P<subject>Each (?:player|opponent)) loses (?P<loss>[1-9]\d*) life unless they '
    r'(?:(?P<discard>discard (?:a card|two cards))|sacrifice (?P<object>(?:a|an) .+?))\.?', re.I,
)


def apnap_object_payment_template(text):
    match = _INSTRUCTION.fullmatch(' '.join(text.strip().split()))
    if match is None:
        return None
    subject = match['subject']
    expected_count = 1
    if match['discard']:
        payment = match['discard'].casefold()
        if payment == "discard two cards":
            if subject.casefold() != "each opponent":
                return None
            expected_count = 2
        template = fixed_affected_player_discard_effect_template(f'{subject} discards {payment.removeprefix("discard ")}.')
    else:
        template = fixed_affected_player_sacrifice_effect_template(f'{subject} sacrifices {match["object"]}.')
        payment = 'sacrifice an eligible permanent'
    if template is None or template.count != expected_count:
        return None
    amount = int(match['loss'])
    effect, = template.effects
    return (f'{template.template_id}-or-life-loss',
            ({**effect, DECLINED_LIFE_LOSS: amount, 'prompt': f'Choose to {payment} or lose {amount} life.'},),
            template.target_schema, template.mechanics)
