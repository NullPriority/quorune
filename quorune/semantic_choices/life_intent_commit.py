from __future__ import annotations
from ..errors import GameRuleError
from ..life_change import prepare_life_change_batch,commit_life_change_batch,LifeChangeRequest,LifeChangeError
from ..replacement import ReplacementChoiceRequired,ReplacementEventBatch
from ..semantic_runtime.life_replacements import collect_life_change_replacement_effects
from ..semantic_runtime.life_prohibitions import collect_life_gain_permissions
from ..semantic_runtime.intents import LifeChangeIntent
from ..semantic_runtime.life_batch_intent import LifeLossBatchIntent
from ..player_result_events import dispatch_life_gain_records

def apply_life_loss_batch_intent(host, intent: LifeLossBatchIntent) -> int:
    from ..effect_runtime.life_effects import _commit
    from ..life_state import LifeChange
    if any(player not in host.active_seats for player in intent.players):
        raise GameRuleError('Life loss batch requires active players')
    prepared=_commit(host,tuple(LifeChange(player,-intent.amount) for player in intent.players),
        effect={'source':intent.source_ref,'_replacement_selections':intent.replacement_selections},actor=intent.actor,reason=intent.reason)
    host._log(intent.actor,'effect.life','Simultaneous life loss resolved.',
        {'players':list(intent.players),'resolved_losses':{r.player:r.amount for r in prepared.records if r.direction=='loss'}},
        importance=1,changed_players=list(intent.players))
    return sum(record.amount for record in prepared.records if record.direction=='loss')


def apply_life_change_intent(host, intent: LifeChangeIntent) -> int:
    try:
        prepared = prepare_life_change_batch(
            host,
            (
                LifeChangeRequest(
                    event_id=(
                        f"semantic.life:{host.state.revision}:"
                        f"{host.state.event_sequence + 1}"
                    ),
                    player=intent.player,
                    amount=intent.amount,
                    source=intent.source_ref,
                    source_controller=intent.actor,
                    cause=intent.reason,
                ),
            ),
            effects=collect_life_change_replacement_effects(host),
            gain_permissions=collect_life_gain_permissions(host),
            selections=intent.replacement_selections,
            require_all_selections=False,
            batch_id=(
                f"replacement:semantic.life:{host.state.revision}:"
                f"{host.state.event_sequence + 1}"
            ),
        )
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
        committed = commit_life_change_batch(host, prepared)
    except LifeChangeError as exc:
        raise GameRuleError(str(exc)) from exc
    record = committed.records[0]
    host._log(
        intent.actor,
        "effect.life",
        f"{intent.player}'s life changed by {record.delta}.",
        {
            "player": intent.player,
            "requested_delta": intent.amount,
            "delta": record.delta,
            "reason": intent.reason,
            "source": intent.source_ref,
        },
        importance=1,
        changed_players=[intent.player],
    )
    dispatch_life_gain_records(host, committed.records)
    return host.state.players[intent.player].life
