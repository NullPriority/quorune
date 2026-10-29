"""Forecast disclosure lifetime, delegated to the canonical hand-reveal owner."""
from .forecast_model import ForecastRevealFrame, FORECAST_REVEAL_FIELD
from .continuous_effect_model import ContinuousObjectIdentity
from .errors import GameRuleError


def begin_forecast_reveal(host, actor, source):
    from .effect_runtime.hand_inspection import begin_scoped_hand_reveal
    if (source.zone != 'hand' or source.owner != actor or source.object_id not in host.state.players[actor].zones['hand']
            or host.state.active_player != actor or (host.state.phase, host.state.step) != ('beginning', 'upkeep')):
        raise GameRuleError('Forecast requires the owners current hand card during their upkeep')
    previous = source.annotations.get(FORECAST_REVEAL_FIELD)
    frame = ForecastRevealFrame.from_dict(previous) if previous is not None else None
    identity = ContinuousObjectIdentity(source.object_id, source.logical_object_id)
    if frame is not None and (frame.subject != identity or frame.owner != actor or frame.turn_sequence != host.state.turn_sequence):
        raise GameRuleError('Forecast reveal scope changed before activation')
    record = host.card_record(source)
    if record is None:raise GameRuleError('Forecast source identity is unavailable')
    selection = {'object_id': source.object_id, 'logical_object_id': source.logical_object_id, 'ref': source.ref,
        'player': actor, 'oracle_id': record.oracle_id, 'name': record.name}
    prior = begin_scoped_hand_reveal(host, selection, summary='A card was revealed to activate Forecast.')
    if frame is None:frame = ForecastRevealFrame(identity, actor, host.state.turn_sequence, prior)
    source.annotations[FORECAST_REVEAL_FIELD] = frame.to_dict()


def end_forecast_reveal(host, source):
    from .effect_runtime.hand_inspection import end_scoped_hand_reveal
    raw = source.annotations.get(FORECAST_REVEAL_FIELD)
    if raw is None:return
    frame = ForecastRevealFrame.from_dict(raw)
    end_scoped_hand_reveal(host, object_id=frame.subject.object_id, logical_object_id=frame.subject.logical_object_id,
        prior_revealed_to=frame.prior_revealed_to)
    source.annotations.pop(FORECAST_REVEAL_FIELD, None)


def synchronize_forecast_reveals(host):
    upkeep = (host.state.phase, host.state.step) == ('beginning', 'upkeep')
    for source in tuple(host.state.cards.values()):
        raw = source.annotations.get(FORECAST_REVEAL_FIELD)
        if raw is None:continue
        frame = ForecastRevealFrame.from_dict(raw)
        if (not upkeep or frame.turn_sequence != host.state.turn_sequence or frame.owner != host.state.active_player
                or source.zone != 'hand' or source.logical_object_id != frame.subject.logical_object_id):
            end_forecast_reveal(host, source)
