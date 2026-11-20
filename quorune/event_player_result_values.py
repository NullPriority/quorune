from __future__ import annotations

"""Closed values permitted by already-bound event-player result layouts."""

from collections.abc import Mapping
from dataclasses import dataclass

from .fractional_quantity_model import HalfQuantitySpec, HALF_LIFE_CAPABILITY
from .public_history_query_model import PublicHistoryQuerySpec
from .replacement.immutable import thaw_value


SPELL_CAST_COUNT_LIFE_KIND = "current_turn_spell_count_life"
SPELL_CAST_COUNT_HISTORY = PublicHistoryQuerySpec("spell_cast", relation="controller")


@dataclass(frozen=True, slots=True)
class SpellCastCountLifeSpec:
    recipient: str
    schema_version: int = 1

    def __post_init__(self):
        if (
            type(self.recipient) is not str or self.recipient not in {"controller", "event_caster"}
            or type(self.schema_version) is not int or self.schema_version != 1
        ):
            raise ValueError("Spell-count life requires an exact recipient and version")

    @property
    def player(self):
        return "$controller" if self.recipient == "controller" else "$context.player"

    @property
    def event_condition(self):
        if self.recipient == "controller":
            return {"field": "controller", "op": "eq", "value": "$source.controller"}
        return None

    @property
    def amount_field(self):
        return "delta" if self.recipient == "controller" else "amount"

    def effect(self):
        return {
            "op": "life" if self.recipient == "controller" else "lose_life",
            "player": self.player,
            self.amount_field: {
                "kind": SPELL_CAST_COUNT_LIFE_KIND, "schema_version": self.schema_version,
                "recipient": self.recipient,
            },
        }

    @classmethod
    def from_effect(cls, effect):
        raw = effect.get("delta", effect.get("amount"))
        if not isinstance(raw, Mapping) or set(raw) != {"kind", "schema_version", "recipient"}:
            raise ValueError("Spell-count life requires one closed amount operand")
        if raw["kind"] != SPELL_CAST_COUNT_LIFE_KIND:
            raise ValueError("Unexpected spell-count life amount")
        spec = cls(raw["recipient"], raw["schema_version"])
        if thaw_value(effect) != spec.effect():
            raise ValueError("Spell-count life requires its matching life result and player")
        return spec


def uses_spell_cast_count_life(effect):
    return isinstance(effect, Mapping) and any(
        isinstance(effect.get(field), Mapping) and effect[field].get("kind") == SPELL_CAST_COUNT_LIFE_KIND
        for field in ("amount", "delta")
    )


def event_result_value_capabilities(operation, field, value, *, committed_damage=False):
    if type(value) is int:
        return () if value > 0 and not committed_damage else None
    if isinstance(value, Mapping) and value.get('kind') == 'public_query_effect_amount':
        from .query_effect_amount_model import PublicQueryAmountSpec, PUBLIC_QUERY_AMOUNT_CAPABILITY
        if committed_damage or (operation, field) not in {
            ('draw', 'count'), ('life', 'delta'), ('lose_life', 'amount'), ('damage', 'amount'),
        }:
            return None
        try:spec = PublicQueryAmountSpec.from_dict(value)
        except (TypeError, ValueError):return None
        return (PUBLIC_QUERY_AMOUNT_CAPABILITY,) if spec.schema_version == 1 and spec.coefficient > 0 else None
    if isinstance(value,Mapping) and value.get('kind')=='scalar_effect_amount':
        from .scalar_effect_amount_model import ScalarEffectAmountSpec,ScalarAmountOrigin,SCALAR_AMOUNT_CAPABILITY
        from .cast_event_amounts import CAST_MANA_VALUE_CAPABILITY
        try:spec=ScalarEffectAmountSpec.from_dict(value)
        except (TypeError,ValueError):return None
        if committed_damage:
            from .received_damage_model import RECEIVED_DAMAGE_CAPABILITY
            return (SCALAR_AMOUNT_CAPABILITY, RECEIVED_DAMAGE_CAPABILITY) if (
                (operation, field) == ('damage', 'amount')
                and spec == ScalarEffectAmountSpec(ScalarAmountOrigin.EVENT_AMOUNT)
            ) else None
        if (spec.origin is not ScalarAmountOrigin.CAST_SPELL_MANA_VALUE or spec.coefficient not in {1,2}
                or (operation,field) not in {('draw','count'),('life','delta'),('lose_life','amount'),('damage','amount')}):return None
        return (SCALAR_AMOUNT_CAPABILITY,CAST_MANA_VALUE_CAPABILITY)
    if committed_damage:
        return None
    if operation != 'lose_life' or field != 'amount':
        return None
    try:
        HalfQuantitySpec.from_dict(value)
    except (TypeError, ValueError):
        return None
    return (HALF_LIFE_CAPABILITY,)
