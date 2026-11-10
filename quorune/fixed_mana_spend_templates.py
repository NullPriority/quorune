from __future__ import annotations

"""Closed production grammar for fixed-output mana with typed spend rules."""

from .mana_spend_rules import ManaSpendRule, ManaSpendRuleKind


_TAILS = {
    "spend this mana only to cast a creature spell of the chosen type": ManaSpendRuleKind.CHOSEN_CREATURE,
    "spend this mana only to cast a creature spell of the chosen type or activate an ability of a creature source of the chosen type": ManaSpendRuleKind.CHOSEN_CREATURE_OR_ABILITY,
    "spend this mana only to cast a creature spell of the chosen type or a legendary creature spell": ManaSpendRuleKind.CHOSEN_OR_LEGENDARY_CREATURE,
    "spend this mana only to cast a creature spell of the chosen type, and that spell can't be countered": ManaSpendRuleKind.CHOSEN_CREATURE_UNCOUNTERABLE,
    "if that mana is spent on an instant or sorcery spell, that spell can't be countered": ManaSpendRuleKind.INSTANT_OR_SORCERY_UNCOUNTERABLE,
    "spend this mana only to cast a spell of the chosen type or activate an ability of a source of the chosen type": ManaSpendRuleKind.CHOSEN_SUBTYPE_SPELL_OR_ABILITY,
    "if that mana is spent on a creature spell, it gains haste": ManaSpendRuleKind.CREATURE_HASTE,
    "if that mana is spent on a creature spell, it gains haste until end of turn": ManaSpendRuleKind.CREATURE_TEMPORARY_HASTE,
    "if that mana is spent on a dragon creature spell, it gains haste until end of turn": ManaSpendRuleKind.DRAGON_CREATURE_TEMPORARY_HASTE,
}


def fixed_mana_spend_rule_template(effect_text):
    output, separator, tail = effect_text.partition(". ")
    if not separator:
        return None
    kind = _TAILS.get(tail.casefold().replace("’", "'").removesuffix("."))
    return (output + ".", ManaSpendRule(kind).encode()) if kind is not None else None
