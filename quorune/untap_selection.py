from __future__ import annotations

"""Controller-owned simultaneous untap sets under fixed overlapping limits."""

from dataclasses import asdict
from typing import Any, Mapping, Sequence
from .errors import GameRuleError
from .model import StackItem
from .replacement.immutable import FrozenMap, immutable_fingerprint, thaw_value
from .tap_state import NEXT_UNTAP_PROHIBITION_ANNOTATION, _exert_players


def _snapshot(host: Any):
    return tuple(FrozenMap({'object_id':c.object_id,'logical_object_id':c.logical_object_id,
        'ref':c.ref,'controller':c.controller,'tapped':c.tapped,'counters':dict(c.counters),
        'annotations':dict(c.annotations),'effective':host._effective_card_data(c)})
        for c in sorted(host.state.cards.values(),key=lambda c:c.object_id)
        if c.zone=='battlefield' and not c.phased_out)


def _eligible(host: Any, plan: Any):
    prohibited = set(plan.prohibited_object_ids)
    return tuple(c for c in sorted(host.state.cards.values(),key=lambda c:c.object_id)
        if c.zone=='battlefield' and not c.phased_out and c.controller==plan.active_player and c.tapped
        and c.object_id not in prohibited and not c.annotations.get(NEXT_UNTAP_PROHIBITION_ANNOTATION,False)
        and plan.active_player not in _exert_players(c))


def selection_schema(host: Any, plan: Any):
    eligible = _eligible(host,plan)
    constrained = {v for limit in plan.selection_limits for v in limit.object_ids}
    optional = set(plan.optional_object_ids)
    choices = tuple(c for c in eligible if c.object_id in constrained or c.object_id in optional)
    refs = {c.object_id:c.ref for c in choices}
    groups = [{'refs':[refs[v] for v in limit.object_ids if v in refs], 'maximum':limit.maximum}
              for limit in plan.selection_limits]
    mandatory = [c.ref for c in choices if c.object_id not in optional]
    return choices, {'type':'object_ref_array','shape':'ref_array','legal_refs':list(refs.values()),
        'minimum':0,'maximum':len(choices),'distinct':True,'subset_maxima':groups,'maximal_refs':mandatory,
        'label':'Permanents to untap'}


def validate_selection(schema: Mapping[str,Any], refs: Any) -> None:
    legal = set(schema['legal_refs'])
    if (type(refs) is not list or any(type(v) is not str for v in refs)
            or len(refs)!=len(set(refs)) or not set(refs)<=legal):
        raise GameRuleError('Untap selection requires distinct currently eligible permanents')
    chosen = set(refs)
    def fits(values):
        return all(len(values.intersection(g['refs']))<=g['maximum'] for g in schema['subset_maxima'])
    if not fits(chosen):
        raise GameRuleError('Untap selection exceeds an applicable limit')
    if any(ref not in chosen and fits(chosen|{ref}) for ref in schema['maximal_refs']):
        raise GameRuleError('Select another eligible permanent while an applicable untap slot remains')


def begin_limited_untap_choice(host: Any, plan: Any, waiting_triggers: Sequence[StackItem]) -> bool:
    if not host.state.config.auto_untap:
        return False
    choices,schema = selection_schema(host,plan)
    mandatory = schema['maximal_refs']
    optional = set(schema['legal_refs'])-set(mandatory)
    try:
        validate_selection(schema,mandatory)
        forced = not optional
    except GameRuleError:
        forced = False
    if forced:
        from .untap_step_coordination import commit_untap_step
        commit_untap_step(host,plan,waiting_triggers,selected_optional_ids=(),
            selected_limit_ids=tuple(c.object_id for c in choices))
        return True
    host.permissions.issue(kind='untap.optional_source',role='pilot',actors=[plan.active_player],allowed_actions=['choose'],
        payload_by_actor={plan.active_player:{'prompt':'Choose permanents to untap within every limit.',
            'objects':[{'id':c.ref,'name':c.printed_name} for c in choices],
            'legal_actions':[{'id':'choose','action':'choose','label':'Untap selected permanents',
                'choice_schema':{'refs':schema}}]}},
        continuation={'untap_selection':{'schema_version':1,'active_player':plan.active_player,
            'turn_sequence':host.state.turn_sequence,'phase_index':host.state.phase_index,
            'snapshot':[thaw_value(v) for v in _snapshot(host)],'plan':asdict(plan),
            'waiting_triggers':[item.to_dict() for item in waiting_triggers]}})
    return True


def complete_limited_untap_choice(host: Any, decision: Any) -> None:
    value = decision.continuation.get('untap_selection')
    fields={'schema_version','active_player','turn_sequence','phase_index','snapshot','plan','waiting_triggers'}
    if (set(decision.continuation) != {'untap_selection'} or decision.kind != 'untap.optional_source'
            or not isinstance(value,Mapping) or set(value)!=fields or type(value['schema_version']) is not int
            or value['schema_version']!=1 or decision.actors!=[value['active_player']]
            or type(value['turn_sequence']) is not int or type(value['phase_index']) is not int
            or type(value['snapshot']) is not list or any(not isinstance(v,Mapping) for v in value['snapshot'])
            or not isinstance(value['plan'],Mapping) or type(value['waiting_triggers']) is not list
            or any(not isinstance(v,Mapping) for v in value['waiting_triggers'])
            or host.state.active_player!=value['active_player'] or host.state.turn_sequence!=value['turn_sequence']
            or host.state.phase_index!=value['phase_index'] or host.state.phase!='beginning' or host.state.step!='untap'):
        raise GameRuleError('Untap selection physical step changed')
    try:
        if immutable_fingerprint(value['snapshot']) != immutable_fingerprint(_snapshot(host)):
            raise GameRuleError('Untap selection subjects or sources changed')
        from .optional_untap import _held_trigger
        triggers = tuple(_held_trigger(FrozenMap(v)) for v in value['waiting_triggers'])
        retained_plan = immutable_fingerprint(value['plan'])
    except (TypeError, ValueError, KeyError) as exc:
        raise GameRuleError('Untap retained state is malformed') from exc
    if len({item.stack_id for item in triggers}) != len(triggers) or len({item.ref for item in triggers}) != len(triggers):
        raise GameRuleError('Untap held triggers must be unique')
    from .semantic_runtime.untap_steps import current_untap_step_plan
    plan = current_untap_step_plan(host,host.state.active_player)
    if immutable_fingerprint(asdict(plan)) != retained_plan:
        raise GameRuleError('Untap participation changed')
    choices,schema = selection_schema(host,plan)
    refs = decision.responses.get(plan.active_player,{}).get('refs')
    validate_selection(schema,refs)
    selected = tuple(c.object_id for c in choices if c.ref in refs)
    from .untap_step_coordination import commit_untap_step
    commit_untap_step(host,plan,triggers,selected_optional_ids=selected,selected_limit_ids=selected)
