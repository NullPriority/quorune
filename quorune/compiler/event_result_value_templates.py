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
