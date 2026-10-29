from __future__ import annotations

"""Observe CR603.8 through ordinary trigger discovery and pending occurrences."""

from collections.abc import Mapping
from ..state_trigger_model import (PublicStateTriggerSpec,STATE_TRIGGER_EVENT,STATE_TRIGGER_FIELD,
    STATE_TRIGGER_CAPABILITY,STATE_TRIGGER_IDENTITY)
from ..source_maintenance import SourceMaintenanceSpec


def state_trigger_capabilities(spec):
    return tuple(sorted({*spec.capabilities,*SourceMaintenanceSpec().capabilities}))


def state_trigger_runtime_dependencies(program):
    marked=isinstance(program.event_condition,Mapping) and program.event_condition.get('field')==STATE_TRIGGER_FIELD
    if program.event!=STATE_TRIGGER_EVENT:
        if marked:raise ValueError('State subscription requires its state event')
        return ()
    spec=PublicStateTriggerSpec.from_condition(program.event_condition)
    if program.active_zone!='battlefield' or program.target_schema is not None or tuple(program.effects)!=(SourceMaintenanceSpec().effect(),):
        raise ValueError('State trigger requires its closed battlefield source-sacrifice result')
    return state_trigger_capabilities(spec)


def _same_occurrence(value,source,program):
    return (value.get('semantic_key')==program.key and value.get('source_object_id')==source.object_id
        and value.get('context',{}).get(STATE_TRIGGER_IDENTITY)==source.logical_object_id)


def state_trigger_available(host,source,program,trigger_batch=None):
    if program.event!=STATE_TRIGGER_EVENT:return True
    spec=PublicStateTriggerSpec.from_condition(program.event_condition)
    if source.zone!='battlefield' or source.phased_out:return False
    if not host._fixed_public_state_condition_holds(source,spec.condition):return False
    for item in (*host.state.stack,*(trigger_batch or ())):
        if _same_occurrence(item.to_dict(),source,program):return False
    for batch in host.state.pending_trigger_batches:
        for item in batch.items:
            if _same_occurrence(item.to_dict(),source,program):return False
    return True


def state_trigger_context(source,program):
    return {STATE_TRIGGER_IDENTITY:source.logical_object_id,'source_logical_object_id':source.logical_object_id} if program.event==STATE_TRIGGER_EVENT else {}


def observe_public_state_triggers(host,*,trigger_batch=None):
    registry=getattr(host,'semantics',None)
    programs=getattr(registry,'programs_for_event',None)
    if not callable(programs) or not programs(STATE_TRIGGER_EVENT):return []
    from ..cast_source_placement_model import cast_source_placement_version
    from ..errors import GameRuleError
    if cast_source_placement_version(getattr(host.state,'cast_source_placement_version',None))!=1:
        raise GameRuleError('Public-state triggers require current casting source placement')
    return host._dispatch_semantic_event(STATE_TRIGGER_EVENT,{STATE_TRIGGER_FIELD:True},
        sources=host._semantic_event_sources(zones={'battlefield'}),trigger_batch=trigger_batch)
