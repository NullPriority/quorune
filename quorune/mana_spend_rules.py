from __future__ import annotations

"""Closed mana spending rules and their production-bound designations."""

from dataclasses import dataclass, replace
from enum import StrEnum

from .mana_restriction_model import _token


MANA_SPEND_RULE_CAPABILITY = "mana.activated.typed_spend_rules"
CHOSEN_SUBTYPE_MANA_CAPABILITY = "mana.activated.chosen_subtype_spending"
PAID_MANA_HASTE_CAPABILITY = "mana.spend.creature_haste"
PAID_MANA_PROTECTION_FIELD = "paid_mana_counter_protection"
_PAID_MANA_HASTE_FIELD = "paid_mana_haste"
MANA_SPEND_RULES = ("106.6", "601.2h", "605.1a", "605.3b", "701.6a", "707.2")
_PREFIX = "mana-spend-rule-v2|"


class ManaSpendRuleKind(StrEnum):
    CHOSEN_CREATURE = "chosen_creature"
    CHOSEN_CREATURE_OR_ABILITY = "chosen_creature_or_ability"
    CHOSEN_OR_LEGENDARY_CREATURE = "chosen_or_legendary_creature"
    CHOSEN_CREATURE_UNCOUNTERABLE = "chosen_creature_uncounterable"
    INSTANT_OR_SORCERY_UNCOUNTERABLE = "instant_or_sorcery_uncounterable"
    CHOSEN_SUBTYPE_SPELL_OR_ABILITY = "chosen_subtype_spell_or_ability"
    CREATURE_HASTE = "creature_haste"
    CREATURE_TEMPORARY_HASTE = "creature_temporary_haste"
    DRAGON_CREATURE_TEMPORARY_HASTE = "dragon_creature_temporary_haste"


class PaidManaHasteLifetime(StrEnum):
    ZONE_OBJECT = "zone_object"
    TURN = "until_end_of_turn"


_HASTE_LIFETIMES = {
    ManaSpendRuleKind.CREATURE_HASTE: PaidManaHasteLifetime.ZONE_OBJECT,
    ManaSpendRuleKind.CREATURE_TEMPORARY_HASTE: PaidManaHasteLifetime.TURN,
    ManaSpendRuleKind.DRAGON_CREATURE_TEMPORARY_HASTE: PaidManaHasteLifetime.TURN,
}


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
        return self.kind not in {ManaSpendRuleKind.INSTANT_OR_SORCERY_UNCOUNTERABLE, *_HASTE_LIFETIMES}

    @property
    def capability_dependencies(self) -> tuple[str, ...]:
        if self.kind in _HASTE_LIFETIMES:
            return (MANA_SPEND_RULE_CAPABILITY, PAID_MANA_HASTE_CAPABILITY)
        if self.kind is ManaSpendRuleKind.CHOSEN_SUBTYPE_SPELL_OR_ABILITY:
            return (MANA_SPEND_RULE_CAPABILITY, CHOSEN_SUBTYPE_MANA_CAPABILITY)
        return (MANA_SPEND_RULE_CAPABILITY,)

    @property
    def is_template(self) -> bool:
        return self.requires_choice and not self.bound

    def encode(self) -> str:
        suffix = f"|{self.chosen_type or '-'}" if self.bound else ""
        return _PREFIX + self.kind.value + suffix

    def allows(self, action, types, subtypes, supertypes) -> bool:
        if not self.requires_choice:
            return True
        if not self.bound:
            return False
        subtype_matches = self.chosen_type is not None and _token(self.chosen_type) in subtypes
        if self.kind is ManaSpendRuleKind.CHOSEN_SUBTYPE_SPELL_OR_ABILITY:
            return action in {"spell", "ability"} and subtype_matches
        if "creature" not in types:
            return False
        if action == "spell":
            if self.kind is ManaSpendRuleKind.CHOSEN_OR_LEGENDARY_CREATURE and "legendary" in supertypes:
                return True
        elif action != "ability" or self.kind is not ManaSpendRuleKind.CHOSEN_CREATURE_OR_ABILITY:
            return False
        return subtype_matches

    def haste_lifetime(self, action, types, subtypes) -> PaidManaHasteLifetime | None:
        if action != "spell" or "creature" not in types:
            return None
        if self.kind is ManaSpendRuleKind.DRAGON_CREATURE_TEMPORARY_HASTE and "dragon" not in subtypes:
            return None
        return _HASTE_LIFETIMES.get(self.kind)

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


def remember_paid_mana_haste(stats, lifetime: PaidManaHasteLifetime):
    previous = stats.get(_PAID_MANA_HASTE_FIELD)
    if previous is not None and PaidManaHasteLifetime(previous) is PaidManaHasteLifetime.ZONE_OBJECT:
        return
    stats[_PAID_MANA_HASTE_FIELD] = lifetime.value


def consume_paid_mana_haste(stats) -> PaidManaHasteLifetime | None:
    if _PAID_MANA_HASTE_FIELD not in stats:
        return None
    return PaidManaHasteLifetime(stats.pop(_PAID_MANA_HASTE_FIELD))
