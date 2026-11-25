"""Compile a source-entry declaration payment using the ordinary cost vocabulary."""

from ..declaration_costs import parse_declaration_cost_line
from ..protected_attack_payment_model import MECHANIC, OPERATION, closed_protected_cost


def next_turn_attack_payment_template(text, *, card_name):
    body = " ".join(text.strip().split())
    prefix = "until your next turn, "
    if not body.casefold().startswith(prefix):
        return None
    parsed = parse_declaration_cost_line(body[len(prefix):], card_name=card_name)
    if not closed_protected_cost(parsed.template):
        return None
    effect = {"op": OPERATION, "schema_version": 1, "cost": parsed.template.to_dict()}
    return ("next-turn-protected-attack-payment-v1", (effect,), None, (MECHANIC,))
