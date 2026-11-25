from __future__ import annotations

"""Resolve both requested life changes through the canonical life transaction."""

from ..continuous_effect_state import ResolutionEffectSource
from ..errors import GameRuleError
from ..rules.player_life_exchange import (
    PLAYER_LIFE_EXCHANGE_OPERATION, PlayerLifeExchangeSpec, plan_player_life_exchange,
    player_life_exchange_program_scope,
)
from ..semantic_runtime.life_prohibitions import collect_life_gain_permissions
def apply_player_life_exchange(host, effect, *, actor, operation, reason):
    from .life_effects import commit_prepared_life_effect_changes, prepare_life_effect_changes

    fields = {"op", "exchange", "players"}
    if (
        set(effect) - {"reason", "_runtime_source", "_replacement_selections"} != fields
        or effect["op"] != PLAYER_LIFE_EXCHANGE_OPERATION
    ):
        raise GameRuleError("The life exchange requires its complete bound instruction")
    try:
        source = ResolutionEffectSource.from_effect(effect)
        spec = PlayerLifeExchangeSpec.from_mapping(effect["exchange"])
    except (ValueError, TypeError) as exc:
        raise GameRuleError(str(exc)) from exc
    item = next((row for row in host.state.stack if row.ref == source.stack_ref), None)
    events = {"activated_ability": "activate", "triggered_ability": "step.begin", "spell": "resolve", "spell_copy": "resolve"}
    if (
        item is None or item.controller != actor or item.kind not in events
        or item.context.get("currently_resolving") is not True or item.context.get("targets_revalidated") is not True
    ):
        raise GameRuleError("The life exchange requires its resolving activation and revalidated targets")
    program = host.semantics.get(item.semantic_key)
    if program is None or program.event != events[item.kind] or not host.semantic_program_is_current_trusted(program):
        raise GameRuleError("The life exchange requires its exact trusted program")
    expected, optional = player_life_exchange_program_scope(program)
    if expected != spec or optional != (item.kind == "triggered_ability"):
        raise GameRuleError("The life exchange must retain its complete printed scope")
    selected = tuple(item.targets)
    players = selected if spec.scope == "two_targets" else (actor, *selected)
    if len(players) != 2 or not isinstance(effect["players"], (tuple, list)) or players != tuple(effect["players"]):
        raise GameRuleError("The life exchange cannot change its revalidated participants")
    lives = {seat: host.state.players[seat].life for seat in host.active_seats if seat in players}
    changes = plan_player_life_exchange(*players, lives, collect_life_gain_permissions(host))
    if not changes:
        return None
    prepared = prepare_life_effect_changes(
        host, changes, effect={**effect, "source": source.card_ref}, actor=actor, reason=reason,
    )
    commit_prepared_life_effect_changes(host, prepared)
    return {seat: host.state.players[seat].life for seat in players}
