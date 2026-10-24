from __future__ import annotations

"""Normalize a current-player quantity only after its subject was bound."""

import re


def event_result_instruction(subject, verb, body):
    if 'half' not in body.casefold():
        return verb.capitalize() + ' ' + body
    pronoun = 'your' if subject == 'you' else 'their'
    match = re.fullmatch(rf'half {pronoun} life, rounded (?P<rounding>up|down)\.?', body, re.IGNORECASE)
    if verb != 'lose' or match is None:
        return None
    return 'You lose half your life, rounded ' + match['rounding'].casefold() + '.'


def scheduled_fractional_life_instruction(body):
    """Normalize the quantity only after the scheduled subject is identified."""
    match = re.fullmatch(r'(?P<subject>that player|they) (?P<verb>loses|lose) '
        r'(?P<value>half their life, rounded (?:up|down)\.?)',body,re.I)
    if match is None:return None
    subject = match['subject'].casefold()
    if match['verb'].casefold() != ('lose' if subject == 'they' else 'loses'):return None
    return event_result_instruction(subject,'lose',match['value'])
