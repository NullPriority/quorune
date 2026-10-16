from __future__ import annotations

from typing import Any, Mapping, Protocol, Sequence

from ..effect_contracts import effect_family_contract
from ..errors import GameRuleError
from ..life_change import (
    commit_life_change_batch,
    LifeBatchResult,
    LifeChangeError,
    LifeChangeHost,
    LifeChangeRequest,
    PreparedLifeChangeBatch,
    prepare_life_change_batch,
    summarize_life_change_batch,
)
from ..life_state import (
    LifeChange,
)
from ..player_result_events import dispatch_life_gain_records
from ..replacement import (
    ReplacementChoiceRequired,
    ReplacementEventBatch,
)
from ..semantic_runtime.life_replacements import (
    collect_life_change_replacement_effects,
    LifeReplacementHost,
)


OPERATIONS = effect_family_contract("life-effects.v2").operations
_LIFE_OPERATION = "".join(("li", "fe"))
_REASON_FIELD = "".join(("rea", "son"))


class LifeEffectObject(Protocol):
    ref: str


class LifeEffectCardRecord(Protocol):
    mana_value: int


class LifeEffectHost(LifeChangeHost, LifeReplacementHost, Protocol):
    active_seats: list[str]

    def _resolve_object(self, actor: str, ref: str) -> LifeEffectObject: ...

    def card_record(
        self, card: LifeEffectObject
    ) -> LifeEffectCardRecord | None: ...


def _commit(
    host: LifeEffectHost,
    changes: Sequence[LifeChange],
    *,
    effect: Mapping[str, Any],
    actor: str,
    reason: str,
) -> PreparedLifeChangeBatch:
    source = effect.get("source")
    source_ref = str(source) if source is not None else None
    cause = str(effect.get("cause") or reason or "effect")
    selections = effect.get("_replacement_selections") or ()
    if not isinstance(selections, Sequence) or isinstance(
        selections, (str, bytes)
    ):
        raise GameRuleError(
            "Life-change replacement selections must be a sequence"
        )
    try:
        prepared = prepare_life_change_batch(
            host,
            tuple(
                LifeChangeRequest(
                    event_id=(
                        f"life.effect:{host.state.revision}:"
                        f"{host.state.event_sequence + 1}:{index}"
                    ),
                    player=change.player,
                    amount=change.amount,
                    source=source_ref,
                    source_controller=actor,
                    cause=cause,
                )
                for index, change in enumerate(changes)
            ),
            effects=collect_life_change_replacement_effects(host),
            selections=selections,
            require_all_selections=False,
            batch_id=(
                f"replacement:life.effect:{host.state.revision}:"
                f"{host.state.event_sequence + 1}"
            ),
        )
    except LifeChangeError as exc:
        raise GameRuleError(str(exc)) from exc
    if prepared.pending is not None:
        raise ReplacementChoiceRequired(
            batch=ReplacementEventBatch(
                batch_id=prepared.batch_id,
                events=prepared.events,
                apnap_order=tuple(host.apnap_order()),
                journal=prepared.journal,
            ),
            effects=prepared.effects,
            pending=prepared.pending,
        )
    try:
        commit_life_change_batch(host, prepared)
    except LifeChangeError as exc:
        raise GameRuleError(str(exc)) from exc
    dispatch_life_gain_records(host, prepared.records)
    return prepared


def _result(prepared: PreparedLifeChangeBatch) -> LifeBatchResult:
    try:
        return summarize_life_change_batch(prepared)
    except LifeChangeError as exc:
        raise GameRuleError(str(exc)) from exc


def _loss_summary(result: LifeBatchResult, players: Sequence[str]) -> str:
    return "; ".join(
        f"{player} lost {result.for_player(player).resolved_loss} "
        f"{_LIFE_OPERATION}"
        for player in players
    )


def _apply_life(
    host: LifeEffectHost,
    effect: Mapping[str, Any],
    *,
    actor: str,
    operation: str,
    reason: str,
) -> int:
    seat = str(effect.get("player") or actor)
    delta = int(effect.get("delta", 0))
    prepared = _commit(
        host,
        (LifeChange(seat, delta),),
        effect=effect,
        actor=actor,
        reason=reason,
    )
    result = _result(prepared)
    player_result = result.for_player(seat)
    host._log(
        actor,
        "effect.life",
        f"{seat}'s life changed by {player_result.delta}.",
        {
            "player": seat,
            "requested_delta": delta,
            "delta": player_result.delta,
            "source": effect.get("source"),
            "cause": effect.get("cause") or reason,
            **result.to_dict(),
        },
        importance=1,
        changed_players=list(result.changed_players),
    )
    return host.state.players[seat].life


def _apply_lose_life(
    host: LifeEffectHost,
    effect: Mapping[str, Any],
    *,
    actor: str,
    operation: str,
    reason: str,
) -> int:
    seat = str(effect.get("player") or actor)
    all_players = "scope" in effect
    if all_players and (
        effect.get("scope") != "all_active_players"
        or "player" in effect
        or type(effect.get("amount")) is not int
        or effect["amount"] < 0
    ):
        raise GameRuleError("Life-loss player scope is unsupported or contradictory")
    players = tuple(host.active_seats) if all_players else (seat,)
    amount = max(0, int(effect.get("amount", 0)))
    prepared = _commit(
        host,
        tuple(LifeChange(player, -amount) for player in players),
        effect=effect,
        actor=actor,
        reason=reason,
    )
    result = _result(prepared)
    if all_players:
        host._log(
            actor,
            "effect.life",
            _loss_summary(result, players) + ".",
            {
                "players": list(players),
                "requested_amount": amount,
                "deltas": {player: result.for_player(player).delta for player in players},
                _REASON_FIELD: reason,
                **result.to_dict(),
            },
            importance=2,
            changed_players=list(result.changed_players),
        )
        return amount
    player_result = result.for_player(seat)
    host._log(
        actor,
        "effect.life",
        f"{seat} lost {player_result.resolved_loss} life.",
        {
            "player": seat,
            "requested_delta": -amount,
            "delta": player_result.delta,
            **result.to_dict(),
        },
        importance=1,
        changed_players=list(result.changed_players),
    )
    return host.state.players[seat].life


def _apply_lose_life_each_opponent(
    host: LifeEffectHost,
    effect: Mapping[str, Any],
    *,
    actor: str,
    operation: str,
    reason: str,
) -> int:
    amount = max(0, int(effect.get("amount", 0)))
    opponents = tuple(seat for seat in host.active_seats if seat != actor)
    prepared = _commit(
        host,
        tuple(LifeChange(opponent, -amount) for opponent in opponents),
        effect=effect,
        actor=actor,
        reason=reason,
    )
    result = _result(prepared)
    host._log(
        actor,
        "effect.life",
        _loss_summary(result, opponents) + ".",
        {
            "opponents": list(opponents),
            "requested_amount": amount,
            "deltas": {
                opponent: result.for_player(opponent).delta
                for opponent in opponents
            },
            _REASON_FIELD: reason,
            **result.to_dict(),
        },
        importance=2,
        changed_players=list(result.changed_players),
    )
    return amount


def _apply_lose_life_equal_mana_value(
    host: LifeEffectHost,
    effect: Mapping[str, Any],
    *,
    actor: str,
    operation: str,
    reason: str,
) -> int:
    seat = str(effect.get("player") or actor)
    card = host._resolve_object(actor, str(effect["card"]))
    record = host.card_record(card)
    amount = int(record.mana_value if record else 0)
    prepared = _commit(
        host,
        (LifeChange(seat, -amount),),
        effect=effect,
        actor=actor,
        reason=reason,
    )
    result = _result(prepared)
    player_result = result.for_player(seat)
    host._log(
        actor,
        "effect.life",
        f"{seat} lost {player_result.resolved_loss} life.",
        {
            "player": seat,
            "requested_delta": -amount,
            "delta": player_result.delta,
            "card": card.ref,
            **result.to_dict(),
        },
        importance=1,
        changed_players=list(result.changed_players),
    )
    return host.state.players[seat].life


def _apply_drain_opponent(
    host: LifeEffectHost,
    effect: Mapping[str, Any],
    *,
    actor: str,
    operation: str,
    reason: str,
) -> int:
    target = str(effect["target"])
    amount = int(effect.get("amount", 1))
    if target not in host.active_seats or target == actor:
        raise GameRuleError("Drain effect requires an active opponent")
    prepared = _commit(
        host,
        (LifeChange(target, -amount), LifeChange(actor, amount)),
        effect=effect,
        actor=actor,
        reason=reason,
    )
    result = _result(prepared)
    target_result = result.for_player(target)
    actor_result = result.for_player(actor)
    host._log(
        actor,
        "effect.life",
        f"{target} lost {target_result.resolved_loss} life and {actor} "
        f"gained {actor_result.resolved_gain} life.",
        {
            "player": target,
            "requested_amount": amount,
            "delta": target_result.delta,
            "gained_by": actor,
            "gained_amount": actor_result.resolved_gain,
            **result.to_dict(),
        },
        importance=2,
        changed_players=list(result.changed_players),
    )
    return amount


def _apply_drain_each_opponent(
    host: LifeEffectHost,
    effect: Mapping[str, Any],
    *,
    actor: str,
    operation: str,
    reason: str,
) -> int:
    amount = int(effect.get("amount", 1))
    opponents = tuple(seat for seat in host.active_seats if seat != actor)
    prepared = _commit(
        host,
        (
            *(LifeChange(opponent, -amount) for opponent in opponents),
            LifeChange(actor, amount),
        ),
        effect=effect,
        actor=actor,
        reason=reason,
    )
    result = _result(prepared)
    actor_result = result.for_player(actor)
    losses = _loss_summary(result, opponents)
    host._log(
        actor,
        "effect.life",
        f"{losses}; {actor} gained {actor_result.resolved_gain} life.",
        {
            "opponents": list(opponents),
            "requested_amount": amount,
            "gained_by": actor,
            "gained_amount": actor_result.resolved_gain,
            "deltas": {
                player.player: player.delta for player in result.players
            },
            **result.to_dict(),
        },
        importance=2,
        changed_players=list(result.changed_players),
    )
    return amount


from .fractional_life import apply_life_loss


HANDLERS = {
    "drain_each_opponent": _apply_drain_each_opponent,
    "drain_opponent": _apply_drain_opponent,
    _LIFE_OPERATION: _apply_life,
    "lose_life": apply_life_loss,
    "lose_life_each_opponent": _apply_lose_life_each_opponent,
    "lose_life_equal_mana_value": _apply_lose_life_equal_mana_value,
}


def apply_effect(
    host: LifeEffectHost,
    effect: Mapping[str, Any],
    *,
    actor: str,
    operation: str,
    reason: str,
) -> Any:
    handler = HANDLERS.get(operation)
    if handler is None:
        raise GameRuleError(f"Unsupported owned effect {operation!r}")
    return handler(
        host,
        effect,
        actor=actor,
        operation=operation,
        reason=reason,
    )


__all__ = ["apply_effect", "HANDLERS", "OPERATIONS"]
