from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from .keyword_abilities import (
    EffectiveKeywordError,
    normalized_effective_keywords as _normalized_effective_keywords,
)


HASTE_KEYWORD = "haste"


class HasteRuleError(ValueError):
    """The current characteristic or control snapshot is malformed."""


class HasteRuleHost(Protocol):
    """Read-only port used by the CR 302.6/702.10 boundary."""

    state: Any

    def _effective_card_data(self, card: Any) -> Mapping[str, Any]: ...

    def _type_parts(
        self, type_line: str
    ) -> tuple[set[str], set[str], set[str]]: ...


def normalized_effective_keywords(
    host: HasteRuleHost,
    card: Any,
) -> frozenset[str]:
    """Return the object's current represented keywords, case-insensitively."""

    try:
        return _normalized_effective_keywords(host, card)
    except EffectiveKeywordError as exc:
        raise HasteRuleError(str(exc)) from exc


def has_effective_haste(host: HasteRuleHost, card: Any) -> bool:
    """Whether current represented characteristics include haste."""

    return HASTE_KEYWORD in normalized_effective_keywords(host, card)


def is_summoning_sick(host: HasteRuleHost, card: Any) -> bool:
    """Evaluate the continuous-control condition in CR 302.6.

    This state is meaningful only for a current creature. Haste does not remove
    the state; it supplies the two exceptions represented by the helpers below.
    """

    data = host._effective_card_data(card)
    if not isinstance(data, Mapping):
        raise HasteRuleError("Effective characteristics must be a mapping")
    type_line = data.get("type_line", "")
    if not isinstance(type_line, str):
        raise HasteRuleError("Effective type line must be a string")
    if "creature" not in host._type_parts(type_line)[0]:
        return False

    controller = getattr(card, "controller", None)
    acquired = getattr(card, "acquired_control_turn_count", None)
    if not isinstance(controller, str) or not controller:
        raise HasteRuleError("Creature controller identity is required")
    # A negative acquisition count is the canonical fixture/setup value for a
    # creature controlled before its controller's first represented turn.
    if type(acquired) is not int:
        raise HasteRuleError("Creature control timestamp is malformed")
    players = getattr(host.state, "players", None)
    if not isinstance(players, Mapping) or controller not in players:
        raise HasteRuleError("Creature controller is not present in state")
    turns_begun = getattr(players[controller], "turns_begun", None)
    if type(turns_begun) is not int or turns_begun < 0:
        raise HasteRuleError("Controller turn count is malformed")
    return turns_begun <= acquired


def summoning_sickness_prohibits_attack(
    host: HasteRuleHost,
    card: Any,
) -> bool:
    """Apply the represented CR 302.6 attack restriction and 702.10b exception."""

    return is_summoning_sick(host, card) and not has_effective_haste(host, card)


def summoning_sickness_prohibits_declaration(host: HasteRuleHost, card: Any) -> bool:
    """Apply the continuous-control restriction and the attack-only exception."""
    from .rules.attached_attack_haste import current_enchanted_attack_permission

    return (
        summoning_sickness_prohibits_attack(host, card)
        and not current_enchanted_attack_permission(host, card)
    )


def summoning_sickness_prohibits_tap_or_untap_cost(
    host: HasteRuleHost,
    card: Any,
    *,
    as_though_haste: bool = False,
) -> bool:
    """Apply CR 302.6 to a source {T}/{Q} cost and the 702.10c exception."""

    if type(as_though_haste) is not bool:
        raise HasteRuleError("As-though-haste permission must be boolean")
    return (
        is_summoning_sick(host, card)
        and not has_effective_haste(host, card)
        and not as_though_haste
    )


__all__ = [
    "HASTE_KEYWORD",
    "HasteRuleError",
    "HasteRuleHost",
    "has_effective_haste",
    "is_summoning_sick",
    "normalized_effective_keywords",
    "summoning_sickness_prohibits_attack",
    "summoning_sickness_prohibits_declaration",
    "summoning_sickness_prohibits_tap_or_untap_cost",
]
