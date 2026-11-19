"""Consume one explicit upward half-life resolution price, without runtime parsing."""
from . import regex_working_set as re
from ..fractional_life_payment_model import FractionalLifePaymentSpec

_REMINDER = ('When another creature is put into your graveyard from the battlefield, '
    'you may pay half your life, rounded up. If you do, return this card from your graveyard '
    'to your hand. Otherwise, exile this card.')


def fractional_life_payment_from_text(text):
    if type(text) is not str or re.fullmatch(r'Pay half your life, rounded up\.?', text.strip(), re.I) is None:
        return None
    return FractionalLifePaymentSpec()


def recover_half_life_price_from_line(line):
    if type(line) is not str:return None
    text = ' '.join(line.strip().split())
    match = re.fullmatch(r'Recover\s*[—–-]\s*(?P<price>Pay half your life, rounded up)\.'
        r'(?:\s+\((?P<reminder>[^()]+)\))?', text, re.I)
    if match is None or match['reminder'] is not None and match['reminder'].casefold() != _REMINDER.casefold():
        return None
    return fractional_life_payment_from_text(match['price'])
