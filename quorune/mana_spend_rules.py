from __future__ import annotations

"""Closed mana spending rules and their production-bound designations."""

from dataclasses import dataclass, replace
from enum import StrEnum

from .mana_restriction_model import _token


MANA_SPEND_RULE_CAPABILITY = "mana.activated.typed_spend_rules"
PAID_MANA_PROTECTION_FIELD = "paid_mana_counter_protection"
MANA_SPEND_RULES = ("106.6", "601.2h", "605.1a", "605.3b", "701.6a", "707.2")
_PREFIX = "mana-spend-rule-v2|"


class ManaSpendRuleKind(StrEnum):
    CHOSEN_CREATURE = "chosen_creature"
    CHOSEN_CREATURE_OR_ABILITY = "chosen_creature_or_ability"
    CHOSEN_OR_LEGENDARY_CREATURE = "chosen_or_legendary_creature"
    CHOSEN_CREATURE_UNCOUNTERABLE = "chosen_creature_uncounterable"
    INSTANT_OR_SORCERY_UNCOUNTERABLE = "instant_or_sorcery_uncounterable"


@dataclass(frozen=True, slots=True)
class ManaSpendRule:
    kind: ManaSpendRuleKind
    bound: bool = False
    chosen_type: str | None = None

    def __post_init__(self):
        if not isinstance(self.kind, ManaSpendRuleKind) or type(self.bound) is not bool:
            raise ValueError("Mana spending rule kind or binding is malformed")
        if self.requires_choice:
            if self.chosen_type is not None:
                from .type_subtype_vocabulary import CREATURE_SUBTYPES

                if not self.bound:
                    raise ValueError("An unbound mana rule cannot carry a chosen type")
                if type(self.chosen_type) is not str or self.chosen_type not in CREATURE_SUBTYPES:
                    raise ValueError("The mana lot must bind a canonical creature subtype")
        elif self.bound or self.chosen_type is not None:
            raise ValueError("Unrestricted mana rider has no source designation")

    @property
    def requires_choice(self) -> bool:
        return self.kind is not ManaSpendRuleKind.INSTANT_OR_SORCERY_UNCOUNTERABLE

    @property
    def is_template(self) -> bool:
        return self.requires_choice and not self.bound

    def encode(self) -> str:
        suffix = f"|{self.chosen_type or '-'}" if self.bound else ""
        return _PREFIX + self.kind.value + suffix

    def allows(self, action, types, subtypes, supertypes) -> bool:
        if not self.requires_choice:
            return True
        if not self.bound or "creature" not in types:
            return False
        if action == "spell":
            if self.kind is ManaSpendRuleKind.CHOSEN_OR_LEGENDARY_CREATURE and "legendary" in supertypes:
                return True
        elif action != "ability" or self.kind is not ManaSpendRuleKind.CHOSEN_CREATURE_OR_ABILITY:
            return False
        return self.chosen_type is not None and _token(self.chosen_type) in subtypes

    def protects(self, action, types, subtypes, supertypes) -> bool:
        if action != "spell":
            return False
        if self.kind is ManaSpendRuleKind.INSTANT_OR_SORCERY_UNCOUNTERABLE:
            return bool(types.intersection({"instant", "sorcery"}))
        return (self.kind is ManaSpendRuleKind.CHOSEN_CREATURE_UNCOUNTERABLE
                and self.allows(action, types, subtypes, supertypes))


def is_typed_mana_spend_rule(value) -> bool:
    return isinstance(value, str) and value.startswith(_PREFIX)


def decode_mana_spend_rule(value) -> ManaSpendRule | None:
    if not is_typed_mana_spend_rule(value):
        return None
    parts = value[len(_PREFIX):].split("|")
    if len(parts) not in (1, 2):
        raise ValueError("Mana spending rule fields are closed")
    chosen = parts[1] if len(parts) == 2 and parts[1] != "-" else None
    rule = ManaSpendRule(ManaSpendRuleKind(parts[0]), len(parts) == 2, chosen)
    if rule.encode() != value:
        raise ValueError("Mana spending rule encoding is not canonical")
    return rule


def bind_mana_spend_rule(value, source):
    """Seal a source-relative choice while its ability produces mana."""
    rule = decode_mana_spend_rule(value)
    if rule is None or not rule.is_template:
        return value
    return replace(rule, bound=True, chosen_type=source.annotations.get("chosen_creature_type")).encode()


def consume_paid_mana_protection(stats):
    if PAID_MANA_PROTECTION_FIELD not in stats:
        return {}
    version = stats.pop(PAID_MANA_PROTECTION_FIELD)
    if type(version) is not int or version != 1:
        raise ValueError("Paid mana counter protection is malformed")
    return {PAID_MANA_PROTECTION_FIELD: version}


def paid_mana_protects_cast(item) -> bool:
    if item.kind != "spell" or PAID_MANA_PROTECTION_FIELD not in item.context:
        return False
    version = item.context[PAID_MANA_PROTECTION_FIELD]
    if type(version) is not int or version != 1:
        raise ValueError("Paid mana counter protection is malformed")
    return True
