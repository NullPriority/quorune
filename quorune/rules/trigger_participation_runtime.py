from __future__ import annotations

"""Event-timed participation reads for the existing trigger-discovery owner."""

from ..ability_fragments import canonical_ability_fragments
from ..characteristic_read_frames import characteristic_read_method
from ..trigger_participation import TriggerMultiplierSpec,StaticTriggerParticipation
from ..trigger_participation_query_model import trigger_participation_rule, trigger_prohibition_can_observe
from .trigger_participation_queries import participation_rule_matches


def trigger_observer_is_prevented(
    host, source, active_zone, characteristics, *, event, context, sources, source_characteristics,
):
    """Read the existing prohibition inputs before the observer can mutate state."""
    _, prevented = typed_trigger_participations(
        host, source=source, controller=source.controller, event=event, context=context,
        sources=sources, source_characteristics=source_characteristics,
        ability_characteristics=characteristics, active_zone=active_zone, prevention_only=True,
    )
    return prevented


def bind_trigger_observer_prevention(host, event, context, sources, source_characteristics):
    from functools import partial

    return partial(trigger_observer_is_prevented, host, event=event, context=context,
                   sources=sources, source_characteristics=source_characteristics)


@characteristic_read_method
def typed_trigger_participations(host,*,source,controller,event,context,sources=None,
        source_characteristics=None,ability_characteristics=None,active_zone='battlefield',
        prevention_only=False):
    if prevention_only and not trigger_prohibition_can_observe(event):
        return (), False
    candidates=sources if sources is not None else host._semantic_event_sources(zones={'battlefield'})
    source_data=ability_characteristics if ability_characteristics is not None else host._effective_card_data(source)
    result=[];prevented=False
    for modifier in candidates:
        if modifier.zone!='battlefield' or modifier.phased_out:continue
        data=(source_characteristics or {}).get(modifier.object_id)
        if data is None:data=host._effective_card_data(modifier)
        fragments=canonical_ability_fragments(data.get('ability_fragments',()))
        specs=[f for f in fragments if isinstance(f,TriggerMultiplierSpec) and f.schema_version==2]
        if not specs:continue
        if any(trigger_participation_rule(f.rule,f.subtype).modifier_condition=='equipped' for f in specs):
            data={**data,'_trigger_participation_equipped':any(
                card.zone=='battlefield' and not card.phased_out and card.attached_to==modifier.ref
                and 'equipment' in host._type_parts(str(((source_characteristics or {}).get(card.object_id)
                    or host._effective_card_data(card)).get('type_line') or ''))[1]
                for card in candidates)}
        for spec in specs:
            if not participation_rule_matches(spec,modifier,data,source,source_data,event,context,active_zone):continue
            if trigger_participation_rule(spec.rule,spec.subtype).outcome=='prevent':prevented=True
            elif not prevention_only:result.append(StaticTriggerParticipation(modifier.object_id,modifier.logical_object_id,
                modifier.controller,'battlefield',spec))
    return tuple(sorted(result,key=lambda value:value.fingerprint)),prevented
