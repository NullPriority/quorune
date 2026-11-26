"""Reassign a public pile choice when its designated opponent concedes."""

from __future__ import annotations

from dataclasses import replace

from ..permissions import current_turn_controller
from ..public_library_piles_model import PublicLibraryPileState
from .model import SemanticChoiceCompletion, SemanticChoiceError
from .public_library_piles import PublicLibraryPilesHandler, _STATE, _effect, _spec


def _departing_opponent_state(host, continuation, actor):
    if continuation.handler_id != PublicLibraryPilesHandler().handler_id or actor == continuation.semantic_frame.controller:
        return None
    player = host.state.players.get(actor)
    if player is None or not player.in_game or current_turn_controller(host.state, actor) is not None:
        return None
    try:
        state = PublicLibraryPileState.from_dict(continuation.effect[_STATE])
        spec = _spec(continuation.effect)
        eligible = (
            state.opponent == actor == spec.player == state.actor(spec)
            and state.controller == continuation.semantic_frame.controller
            and state.stack_ref == continuation.stack_ref
            and state.stage in {"divide", "choose_pile"}
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise SemanticChoiceError(str(exc)) from exc
    return (state, spec) if eligible else None


def pile_concession_offer(host, continuation, actor):
    if _departing_opponent_state(host, continuation, actor) is None:
        return None
    from ..rules.action_catalog import _concede_offer

    return _concede_offer(host, actor)


def concede_during_pile_choice(host, *, item, continuation, actor, response):
    departing = _departing_opponent_state(host, continuation, actor)
    if departing is None or response.get("action") != "concede":
        raise SemanticChoiceError("Only the designated opponent can concede during this pile decision")

    from ..turn_priority_owner import commit_confirmed_concession
    from .counter_coordination import continue_semantic_completion

    state, spec = departing
    commit_confirmed_concession(host, actor, response)
    if host.state.game_over:
        return
    next_state = replace(state, stage="choose_opponent", opponent=None)
    completion = SemanticChoiceCompletion(repeat_effect=_effect(continuation.effect, spec, next_state))
    continue_semantic_completion(
        host, item=item, continuation=continuation, actor=actor, response=response, completion=completion,
    )
