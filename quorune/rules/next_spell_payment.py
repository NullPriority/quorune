from __future__ import annotations

"""Closed result and turn window for the next spell's payment ability."""

from dataclasses import dataclass
from typing import Any, Mapping
from ..casting_payment_keywords import PAYMENT_GRANT_SCOPE_FIELD


NEXT_SPELL_PAYMENT_OPERATION = "grant_next_spell_payment"
NEXT_SPELL_PAYMENT_CAPABILITY = "casting.payment.next_spell_grant"
NEXT_SPELL_PAYMENT_MECHANIC = "fixed-next-spell-payment-grant"
NEXT_SPELL_PAYMENT_FIELD = "next_spell_payment"
_LEGACY_IMPROVISE_FIELD = "next_spell_improvise"
_KINDS = frozenset({"convoke", "improvise"})


def next_spell_payment_kind(effect: Mapping[str, Any], *, actor: str = "$controller") -> str:
    if (not isinstance(effect, Mapping) or set(effect) != {"op", "schema_version", "player", "kind"}
            or effect["op"] != NEXT_SPELL_PAYMENT_OPERATION
            or type(effect["schema_version"]) is not int or effect["schema_version"] != 1
            or effect["player"] != actor or type(effect["kind"]) is not str or effect["kind"] not in _KINDS):
        raise ValueError("Next spell payment requires a closed controller result")
    return effect["kind"]


def next_spell_payment_payload_dependencies(effect: Mapping[str, Any]) -> tuple[str, ...]:
    kind = next_spell_payment_kind(effect)
    return NEXT_SPELL_PAYMENT_CAPABILITY, f"casting.payment.{kind}"


def next_spell_payment_node_capabilities(*, effects, target_schema, mechanic_ids, **_kwargs):
    if target_schema is not None or len(effects) != 1 or NEXT_SPELL_PAYMENT_MECHANIC not in mechanic_ids:
        return ()
    try:
        return next_spell_payment_payload_dependencies(effects[0])
    except (TypeError, ValueError):
        return ()


def is_closed_next_spell_payment_program(program):
    required = next_spell_payment_node_capabilities(effects=program.effects,
        target_schema=program.target_schema, mechanic_ids=program.coverage)
    return bool(required) and set(required) <= set(program.capability_dependencies)


def temporary_player_rule_node_capabilities(**shape):
    from .cast_cascade_grants import cast_cascade_node_capabilities
    from .temporary_cast_restrictions import temporary_cast_node_capabilities

    return (next_spell_payment_node_capabilities(**shape) or cast_cascade_node_capabilities(**shape)
            or temporary_cast_node_capabilities(**shape))


def temporary_cast_grant_covered_mechanics(supplied):
    from .cast_cascade_grants import CAST_CASCADE_CAPABILITY, CAST_CASCADE_MECHANIC

    return {mechanic for capability, mechanic in ((NEXT_SPELL_PAYMENT_CAPABILITY, NEXT_SPELL_PAYMENT_MECHANIC),
        (CAST_CASCADE_CAPABILITY, CAST_CASCADE_MECHANIC)) if capability in supplied}


def expire_temporary_cast_ability_grants(stats):
    from .cast_cascade_grants import expire_cast_cascade_grants

    expire_next_spell_payments(stats)
    expire_cast_cascade_grants(stats)


@dataclass(frozen=True, slots=True)
class NextSpellPaymentWindow:
    turn_sequence: int
    kinds: tuple[str, ...]

    def __post_init__(self) -> None:
        if (type(self.turn_sequence) is not int or self.turn_sequence < 0 or not self.kinds
                or any(type(kind) is not str or kind not in _KINDS for kind in self.kinds)
                or self.kinds != tuple(sorted(set(self.kinds)))):
            raise ValueError("Next spell payment window is malformed")

    def to_dict(self) -> dict[str, Any]:
        return {"schema_version": 1, "turn_sequence": self.turn_sequence, "kinds": list(self.kinds)}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> NextSpellPaymentWindow:
        if (not isinstance(value, Mapping) or set(value) != {"schema_version", "turn_sequence", "kinds"}
                or type(value["schema_version"]) is not int or value["schema_version"] != 1
                or not isinstance(value["kinds"], list)):
            raise ValueError("Next spell payment window fields are closed")
        return cls(value["turn_sequence"], tuple(value["kinds"]))


def _current_payment_kinds(stats, turn_sequence):
    if NEXT_SPELL_PAYMENT_FIELD not in stats:
        return ()
    window = NextSpellPaymentWindow.from_dict(stats[NEXT_SPELL_PAYMENT_FIELD])
    if window.turn_sequence > turn_sequence:
        raise ValueError("Next spell payment window belongs to a future turn")
    return window.kinds if window.turn_sequence == turn_sequence else ()


def grant_next_spell_payment(stats, turn_sequence, kind):
    current = _current_payment_kinds(stats, turn_sequence)
    stats[NEXT_SPELL_PAYMENT_FIELD] = NextSpellPaymentWindow(
        turn_sequence, tuple(sorted({*current, kind})),
    ).to_dict()


def with_next_spell_payments(stats, turn_sequence, mechanics):
    current = _current_payment_kinds(stats, turn_sequence)
    granted = set(current)
    if stats.get(_LEGACY_IMPROVISE_FIELD):
        granted.add("improvise")
    if not granted:
        return mechanics
    existing = {str(value.get("kind") or "").casefold() for value in mechanics}
    payments = [*mechanics, *({"kind": kind, "schema_version": 1} for kind in sorted(granted - existing))]
    return [{**value, PAYMENT_GRANT_SCOPE_FIELD: "next_spell"} if value.get("kind") in current else value
            for value in payments]


def capture_next_spell_payments(stats, turn_sequence, *, preparing):
    kinds = _current_payment_kinds(stats, turn_sequence)
    legacy_improvise = bool(stats.get(_LEGACY_IMPROVISE_FIELD, False))
    if not preparing:
        expire_next_spell_payments(stats)
    return legacy_improvise, kinds


def expire_next_spell_payments(stats):
    stats.pop(_LEGACY_IMPROVISE_FIELD, None)
    stats.pop(NEXT_SPELL_PAYMENT_FIELD, None)
