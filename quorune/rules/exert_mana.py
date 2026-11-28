"""The closed red-priced tap and exert ability with a paid haste rider."""

from ..errors import GameRuleError
from ..fixed_mana_results import EXERT_MANA_SOURCE
from ..fixed_mana_spend_templates import fixed_mana_spend_rule_template
from ..mana_spend_rules import ManaSpendRule, ManaSpendRuleKind


EXERT_MANA_HANDLER_ID = "ability.activated.mana.exert-source.v7"
EXERT_PRICE_TEXT = "{r}, {t}, exert this land"
EXERT_MANA_RESTRICTION = ManaSpendRule(ManaSpendRuleKind.CREATURE_TEMPORARY_HASTE).encode()
EXERT_MANA_PRICE = dict.fromkeys(("GENERIC", *"WUBRGC"), 0)
EXERT_MANA_PRICE["R"] = 1


def is_exert_mana(ability):
    result = ability.fixed_mana_result
    return result is not None and result.kind == EXERT_MANA_SOURCE


def has_exact_exert_mana_text(cost_text, effect_text):
    return (" ".join(cost_text.split()).casefold() == EXERT_PRICE_TEXT
            and fixed_mana_spend_rule_template(" ".join(effect_text.split()))
                == ("Add {R}{R}.", EXERT_MANA_RESTRICTION))


def apply_mana_exert(host, source, *, actor):
    from ..tap_state import TapStateError, exert_permanent

    if source is None or source.controller != actor or not source.tapped:
        raise GameRuleError("Exert mana needs its just-tapped source under the payer's control")
    try:
        exert_permanent(host, source, actor=actor, linked_program=None, trigger_batch=None)
    except TapStateError as exc:
        raise GameRuleError(str(exc)) from exc
