from __future__ import annotations

"""Evaluation of compiler-pinned activated-ability conditions."""

from collections.abc import Mapping
from typing import Any, Protocol

from ...abilities import (
    ActivatedAbility,
    ActivationCondition,
    ActivationConditionKind,
)
from ...activation_usage import (
    ActivationUsageError,
    activation_usage_verdict,
)
from ...object_query import object_matches_query, object_query_result


class ActivationConditionHost(Protocol):
    state: Any

    def _effective_card_data(self, card: Any) -> dict[str, Any]: ...

    def _type_parts(
        self, type_line: str
    ) -> tuple[set[str], set[str], set[str]]: ...


def _effective_card_types(
    host: ActivationConditionHost,
    card: Any,
) -> frozenset[str] | None:
    """Return the canonical effective card types or fail closed."""

    try:
        data = host._effective_card_data(card)
    except (AttributeError, KeyError, TypeError, ValueError):
        return None
    if not isinstance(data, Mapping):
        return None
    type_line = data.get("type_line")
    if not isinstance(type_line, str) or not type_line.strip():
        return None
    try:
        parts = host._type_parts(type_line)
    except (AttributeError, TypeError, ValueError):
        return None
    if not isinstance(parts, tuple) or len(parts) != 3:
        return None
    card_types = parts[0]
    if not isinstance(card_types, (set, frozenset)) or any(
        not isinstance(value, str) or not value.strip()
        for value in card_types
    ):
        return None
    return frozenset(value.casefold() for value in card_types)


def _public_query_count(
    host: ActivationConditionHost,
    seat: str,
    condition: ActivationCondition,
) -> int | None:
    query = condition.query
    if query is None:
        return None
    zone = query.zones[0]
    try:
        object_ids = tuple(host.state.players[seat].zones[zone])
    except (AttributeError, KeyError, TypeError):
        return None
    if zone == "hand":
        return len(object_ids)
    count = 0
    for object_id in object_ids:
        try:
            card = host.state.cards[object_id]
            if zone == "battlefield" and (
                card.controller != seat or card.phased_out
            ):
                continue
            effective = host._effective_card_data(card)
            type_parts = host._type_parts(
                str(effective.get("type_line") or "")
            )
            attached = host.state.cards.get(card.attached_to or "")
            row = object_query_result(
                card,
                effective,
                type_parts=type_parts,
                known_to_actor=True,
                attached_to_ref=(
                    attached.ref if attached is not None else None
                ),
            )
        except (AttributeError, KeyError, TypeError, ValueError):
            return None
        if object_matches_query(row, query):
            count += 1
    return count


def _typed_condition_status(
    host: ActivationConditionHost,
    seat: str,
    condition: ActivationCondition,
) -> tuple[str, str | None]:
    kind = condition.kind
    if kind is ActivationConditionKind.CONTROLLERS_TURN:
        return (
            ("payable", None)
            if host.state.active_player == seat
            else ("unavailable", "only_during_your_turn")
        )
    if kind is ActivationConditionKind.CONTROLLERS_UPKEEP:
        return (
            ("payable", None)
            if (
                host.state.active_player == seat
                and host.state.phase == "beginning"
                and host.state.step == "upkeep"
            )
            else ("unavailable", "only_during_your_upkeep")
        )
    if kind is ActivationConditionKind.CONTROLLERS_TURN_BEFORE_ATTACKERS:
        before_attackers = (
            (host.state.phase == "beginning")
            or (host.state.phase, host.state.step)
            in {
                ("precombat_main", "main"),
                ("combat", "beginning_combat"),
            }
        )
        return (
            ("payable", None)
            if host.state.active_player == seat and before_attackers
            else ("unavailable", "only_before_attackers_are_declared")
        )
    if kind is ActivationConditionKind.NOT_CONTROLLERS_TURN:
        return (
            ("unavailable", "only_during_another_players_turn")
            if host.state.active_player == seat
            else ("payable", None)
        )
    if kind is ActivationConditionKind.TOKEN_CREATED_THIS_TURN:
        created = int(
            host.state.players[seat].stats.get(
                "tokens_created_by_turn", {}
            ).get(str(host.state.turn_sequence), 0)
        )
        return (
            ("payable", None)
            if created > 0
            else ("unavailable", "requires_token_created_this_turn")
        )
    if kind is ActivationConditionKind.CONTROLS_TYPE:
        controlled = 0
        for object_id in host.state.players[seat].zones["battlefield"]:
            permanent = host.state.cards[object_id]
            if permanent.controller != seat or permanent.phased_out:
                continue
            card_types = _effective_card_types(host, permanent)
            if card_types is None:
                return "unresolved", "malformed_effective_type_line"
            if condition.card_type in card_types:
                controlled += 1
        required = int(condition.minimum or 0)
        return (
            ("payable", None)
            if controlled >= required
            else (
                "unavailable",
                f"requires_{required}_{condition.card_type}s",
            )
        )
    if kind is ActivationConditionKind.GRAVEYARD_DISTINCT_TYPES:
        card_types: set[str] = set()
        for object_id in host.state.players[seat].zones["graveyard"]:
            object_types = _effective_card_types(host, object_id)
            if object_types is None:
                return "unresolved", "malformed_effective_type_line"
            card_types.update(object_types)
        return (
            ("payable", None)
            if len(card_types) >= int(condition.minimum or 0)
            else ("unavailable", "requires_delirium")
        )
    if kind is ActivationConditionKind.PUBLIC_QUERY_COUNT:
        count = _public_query_count(host, seat, condition)
        if count is None:
            return "unresolved", "malformed_public_activation_query"
        minimum = int(condition.minimum or 0)
        maximum = condition.maximum
        return (
            ("payable", None)
            if count >= minimum and (maximum is None or count <= maximum)
            else ("unavailable", "requires_public_activation_query")
        )
    return "unresolved", "unresolved_activation_condition"


def activation_condition_status(
    host: ActivationConditionHost,
    seat: str,
    ability: ActivatedAbility,
    source: Any | None = None,
) -> tuple[str, str | None]:
    """Evaluate closed predicates and usage limits without Oracle text reads."""

    if ability.activation_limit is not None:
        if source is None:
            return "unresolved", "activation_source_required"
        try:
            usage = activation_usage_verdict(
                source,
                ability_id=ability.ability_id,
                limit=ability.activation_limit,
                turn_sequence=host.state.turn_sequence,
            )
        except ActivationUsageError:
            return "unresolved", "malformed_activation_usage"
        if not usage.available:
            return "unavailable", usage.reason
    for condition in ability.activation_conditions:
        status, reason = _typed_condition_status(host, seat, condition)
        if status != "payable":
            return status, reason
    return "payable", None


__all__ = ["ActivationConditionHost", "activation_condition_status"]
