"""Resolve setting life through the existing replacement-capable life batch."""
from collections.abc import Mapping
from ..errors import GameRuleError
from ..life_total_setting_model import LifeTotalValueSpec,plan_life_total_setting
from ..life_state import LifeChange


def _counts(host,kind,players):
    result={}
    for player in players:
        zones=host.state.players[player].zones
        if kind=='library':
            result[player]=len(zones['library']);continue
        cards=tuple(host.state.cards[oid] for oid in zones['battlefield'])
        if any(card.zone!='battlefield' or card.controller!=player for card in cards):
            raise GameRuleError('Life-total count snapshot has contradictory battlefield membership')
        if any(type(card.phased_out) is not bool for card in cards):
            raise GameRuleError('Life-total count snapshot requires known phasing status')
        cards=tuple(card for card in cards if not card.phased_out)
        result[player]=(len(cards) if kind=='permanents' else sum('creature' in
            host._type_parts(str(host._effective_card_data(card).get('type_line') or ''))[0] for card in cards))
    return result


def apply_set_life_total(host,effect,*,actor,operation,reason):
    from .life_effects import _commit,_result
    allowed={'op','schema_version','value','player','scope','source','cause','reason','_replacement_selections','_runtime_source'}
    if (set(effect)-allowed or effect.get('op')!='set_life_total'
            or type(effect.get('schema_version')) is not int or effect['schema_version']!=1
            or ('player' in effect)==('scope' in effect) or actor not in host.active_seats):
        raise GameRuleError('Life-total setting requires its closed current instruction')
    try:value=LifeTotalValueSpec.from_dict(effect['value'])
    except (ValueError,TypeError,KeyError) as exc:raise GameRuleError(str(exc)) from exc
    if '_runtime_source' in effect:
        from ..semantic_runtime.context import SemanticSourceContext,SemanticNodeError
        raw=effect['_runtime_source']
        if not isinstance(raw,Mapping) or set(raw)!={'stack_ref','object_id','logical_object_id','card_ref'}:
            raise GameRuleError('Life-total setting has malformed authoritative source context')
        try:source=SemanticSourceContext(**raw)
        except (SemanticNodeError,TypeError) as exc:raise GameRuleError(str(exc)) from exc
        if effect.get('source') is not None and effect['source']!=source.card_ref:
            raise GameRuleError('Life-total setting source contradicts its authoritative context')
        effect={**effect,'source':source.card_ref}
    if 'scope' in effect:
        if effect['scope']!='all_active_players':raise GameRuleError('Life-total player scope is unsupported')
        players=tuple(host.apnap_order())
    else:
        if type(effect['player']) is not str or effect['player'] not in host.active_seats:
            raise GameRuleError('Life-total setting requires a surviving affected player')
        players=(effect['player'],)
    lives={seat:host.state.players[seat].life for seat in host.active_seats}
    counts=_counts(host,value.kind,players) if value.kind in {'creatures','permanents','library'} else None
    try:transitions=plan_life_total_setting(value,players,lives,starting_life=host.state.config.starting_life,counts=counts)
    except (ValueError,TypeError) as exc:raise GameRuleError(str(exc)) from exc
    prepared=_commit(host,tuple(LifeChange(item.player,item.delta) for item in transitions),
        effect=effect,actor=actor,reason=reason)
    result=_result(prepared)
    host._log(actor,'effect.life','Life-total setting resolved.',
        {'players':list(players),'requested_totals':{item.player:item.requested_total for item in transitions},
         'deltas':{seat:result.for_player(seat).delta for seat in players},'value':value.to_dict(),**result.to_dict()},
        importance=2,changed_players=list(result.changed_players))
    return {seat:host.state.players[seat].life for seat in players}
