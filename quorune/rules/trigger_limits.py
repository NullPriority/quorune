from __future__ import annotations

"""Closed usage limits for compiled triggered abilities."""

from dataclasses import dataclass
from typing import Any, Mapping

TRIGGER_ONCE_PER_TURN_CAPABILITY = "trigger.usage.once_per_turn"
DEPARTURE_TRIGGER_LIMIT_CAPABILITY = 'trigger.usage.departure_incarnation'
OPTIONAL_RESULT_LIMIT_CAPABILITY = 'trigger.usage.once_per_turn_optional_result'
OPTIONAL_RESULT_LIMIT_KIND = 'optional_result_once_per_turn'
EVENT_SOURCE_USAGE_CONTEXT = 'event_source_trigger_usage'
OPTIONAL_RESULT_USED_HISTORY = 'trigger_optional_result_used'
OPTIONAL_RESULT_CHILDREN = frozenset({'draw', 'discover'})
_DEPARTURE_EVENTS = frozenset({'artifact.graveyard','permanent.graveyard','permanent.leave',
    'creature.dies','permanent.sacrificed'})
UNSUPPORTED_LIMITED_TRIGGER_EVENTS = frozenset({
    "unresolved", "resolve", "activate", "multi.event", "artifact.graveyard",
    "permanent.graveyard", "permanent.leave", "creature.dies", "spell.countered",
    "permanent.sacrificed",
})


def supported_trigger_limit_event(event: str) -> bool:
    return bool(type(event) is str and event
        and event.removesuffix(".self") not in UNSUPPORTED_LIMITED_TRIGGER_EVENTS)


def supported_trigger_limit_subscriptions(event: str, condition: Mapping[str, Any] | None) -> bool:
    from .event_subscriptions import FixedEventSubscriptionSet
    if not supported_trigger_limit_event(event):
        return False
    subscriptions=FixedEventSubscriptionSet.from_condition(condition)
    return subscriptions is None or all(supported_trigger_limit_event(child.event) for child in subscriptions.subscriptions)


@dataclass(frozen=True, slots=True)
class TriggerLimitSpec:
    schema_version: int = 1
    kind: str = "once_per_turn"
    source_scope: str | None = None

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version not in {1, 2, 3}:
            raise ValueError("Unsupported trigger-limit schema version")
        expected_kind = OPTIONAL_RESULT_LIMIT_KIND if self.schema_version == 3 else 'once_per_turn'
        if self.kind != expected_kind:
            raise ValueError("Unsupported trigger usage limit")
        expected_scope = 'event_incarnation' if self.schema_version == 2 else None
        if self.source_scope != expected_scope:
            raise ValueError('Trigger usage source scope does not match its version')

    def to_dict(self) -> dict[str, Any]:
        return {"schema_version": self.schema_version, "kind": self.kind,
            **({'source_scope':self.source_scope} if self.schema_version==2 else {})}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> TriggerLimitSpec:
        if (not isinstance(value, Mapping) or set(value)!=(
                {'schema_version','kind','source_scope'} if value.get('schema_version')==2
                else {'schema_version','kind'})):
            raise ValueError('Trigger limit has a closed versioned field set')
        return cls(schema_version=value['schema_version'],kind=value['kind'],source_scope=value.get('source_scope'))


def departure_trigger_limit_event(event: str, condition: Mapping[str,Any] | None) -> bool:
    from .event_subscriptions import FixedEventSubscriptionSet
    return bool(type(event) is str and event.removesuffix('.self') in _DEPARTURE_EVENTS
        and FixedEventSubscriptionSet.from_condition(condition) is None)


def trigger_limit_capabilities(value: Mapping[str,Any]) -> tuple[str,...]:
    spec=TriggerLimitSpec.from_dict(value)
    if spec.schema_version == 3:
        return (OPTIONAL_RESULT_LIMIT_CAPABILITY,)
    return (TRIGGER_ONCE_PER_TURN_CAPABILITY,*((DEPARTURE_TRIGGER_LIMIT_CAPABILITY,) if spec.schema_version==2 else ()))


def supported_optional_result_limit(program: Any) -> bool:
    if program.target_schema is not None or len(program.effects) != 1:
        return False
    effect = program.effects[0]
    children = effect.get('effects')
    if (effect.get('op') != 'offer_optional_effect' or set(effect) != {'op', 'player', 'effects'}
            or effect['player'] != '$controller' or not isinstance(children, (list, tuple))
            or len(children) != 1 or not isinstance(children[0], Mapping)):
        return False
    child = children[0]
    if child.get('op') in OPTIONAL_RESULT_CHILDREN:
        return child.get('player') == '$controller'
    if child.get('op') == 'life' and child.get('player') == '$controller':
        from ..scalar_effect_amount_model import ScalarEffectAmountSpec

        amount = child.get('delta')
        if type(amount) is int:
            return amount > 0
        try:
            return ScalarEffectAmountSpec.from_dict(amount).coefficient > 0
        except (ValueError, TypeError):
            return False
    if child.get('op') == 'copy_stack_item':
        return dict(child) == {'op': 'copy_stack_item', 'stack': '$context.stack',
                               'copy_source': '$context.event_spell_copy_source'}
    if child.get('op') != 'offer_optional_mana_payment' or child.get('player') != '$controller':
        return False
    from ..fixed_effect_payment import FixedEffectPaymentSpec

    try:
        payment = FixedEffectPaymentSpec.from_dict(child.get('payment'), allow_mandatory=True)
    except (ValueError, TypeError):
        return False
    return payment.mandatory and payment.predicate.owner == '$controller'


def validate_program_trigger_limit(program: Any) -> None:
    if program.trigger_limit is None:
        return
    spec=TriggerLimitSpec.from_dict(program.trigger_limit)
    permitted=(departure_trigger_limit_event(program.event,program.event_condition) if spec.schema_version==2
        else supported_trigger_limit_subscriptions(program.event,program.event_condition))
    if spec.schema_version == 3 and not supported_optional_result_limit(program):
        raise ValueError('Optional result limits require one supported controller choice')
    if program.active_zone != "battlefield" or not permitted:
        raise ValueError('Trigger limits require a supported '+(
            'event-incarnation battlefield departure' if spec.schema_version==2 else 'nondeparture battlefield event'))
    if not set(trigger_limit_capabilities(program.trigger_limit))<=set(program.capability_dependencies):
        raise ValueError("Trigger limits require their usage capability dependency")


def event_source_usage_context(source: Any, program: Any, turn_sequence: int) -> dict[str,Any]:
    if program.trigger_limit is None or TriggerLimitSpec.from_dict(program.trigger_limit).schema_version == 1:
        return {}
    validate_program_trigger_limit(program)
    if type(turn_sequence) is not int or turn_sequence<0:raise ValueError('Trigger usage turn is malformed')
    return {EVENT_SOURCE_USAGE_CONTEXT:{'schema_version':1,'object_id':source.object_id,
        'logical_object_id':source.logical_object_id,'program_key':program.key,
        'ability_id':program.ability_id,'turn_sequence':turn_sequence}}


def _pending_usage(item: Any) -> Mapping[str,Any] | None:
    value=item.context.get(EVENT_SOURCE_USAGE_CONTEXT)
    if value is None:return None
    fields={'schema_version','object_id','logical_object_id','program_key','ability_id','turn_sequence'}
    if (not isinstance(value,Mapping) or set(value)!=fields or type(value['schema_version']) is not int
            or value['schema_version']!=1 or type(value['turn_sequence']) is not int or value['turn_sequence']<0
            or any(type(value[key]) is not str or not value[key] for key in fields-{'schema_version','turn_sequence'})
            or value['object_id']!=item.source_object_id or value['program_key']!=item.semantic_key):
        raise ValueError('Pending event-source usage claim is malformed')
    return value


@dataclass(frozen=True,slots=True)
class TriggerUsagePlan:
    live_source: Any | None
    allows_multipliers: bool = False


def _optional_result_available(state: Any, source_identity: str, program_key: str) -> bool:
    from ..turn_history import current_turn_history_events

    if state.turn_history is None:
        raise ValueError('Optional trigger result requires authoritative turn history')
    events = current_turn_history_events(state.turn_history, turn_sequence=state.turn_sequence,
                                        kind=OPTIONAL_RESULT_USED_HISTORY)
    return not any(event.object_incarnation == source_identity and event.target == program_key for event in events)


def optional_result_usage_binding(host: Any, item: Any) -> Mapping[str, Any] | None:
    program = host.semantics.get(item.semantic_key)
    if program is None or program.trigger_limit is None:
        return None
    if TriggerLimitSpec.from_dict(program.trigger_limit).schema_version != 3:
        return None
    validate_program_trigger_limit(program)
    binding = _pending_usage(item)
    if (item.kind != 'triggered_ability' or binding is None or binding['ability_id'] != program.ability_id
            or binding['logical_object_id'] != item.context.get('source_logical_object_id')
            or binding['turn_sequence'] > host.state.turn_sequence):
        raise ValueError('Optional trigger result lost its event-source usage identity')
    object_id, _, incarnation = binding['logical_object_id'].rpartition('@')
    if (object_id != binding['object_id'] or not incarnation.isascii() or not incarnation.isdecimal()
            or len(incarnation) > 1 and incarnation.startswith('0')):
        raise ValueError('Optional trigger result source incarnation is malformed')
    return binding


def optional_result_usage_available(state: Any, binding: Mapping[str, Any]) -> bool:
    return _optional_result_available(state, binding['logical_object_id'], binding['program_key'])


def commit_optional_result_usage(host: Any, item: Any, actor: str) -> None:
    binding = optional_result_usage_binding(host, item)
    if binding is None:
        return
    if actor != item.controller or not optional_result_usage_available(host.state, binding):
        raise ValueError('Optional trigger result is unavailable to this controller')
    host._record_turn_history(OPTIONAL_RESULT_USED_HISTORY, actor=actor,
        object_incarnation=binding['logical_object_id'], target=binding['program_key'], target_kind='ability_program')


def trigger_usage_plan(state: Any, source: Any, program: Any, pending: Any) -> TriggerUsagePlan | None:
    """Read captured use and accepted batch claims without mutating LKI."""
    from ..activation_usage import trigger_usage_available
    spec=TriggerLimitSpec.from_dict(program.trigger_limit)
    validate_program_trigger_limit(program)
    if spec.schema_version == 3:
        if not _optional_result_available(state, source.logical_object_id, program.key):
            return None
        return TriggerUsagePlan(None, allows_multipliers=True)
    live=state.cards.get(source.object_id)
    current=live is not None and live.logical_object_id==source.logical_object_id
    if spec.schema_version==1:
        return (TriggerUsagePlan(live) if current and trigger_usage_available(live,
            ability_id=program.ability_id,turn_sequence=state.turn_sequence) else None)
    if not trigger_usage_available(source,ability_id=program.ability_id,turn_sequence=state.turn_sequence):return None
    for item in pending:
        claim=_pending_usage(item)
        if claim is None:
            if item.semantic_key==program.key and item.source_object_id==source.object_id:
                raise ValueError('Pending departure trigger is missing its event-source usage claim')
            continue
        if claim['turn_sequence']>state.turn_sequence:raise ValueError('Pending trigger usage belongs to a future turn')
        if (claim['object_id']==source.object_id and claim['logical_object_id']==source.logical_object_id
                and claim['program_key']==program.key and claim['ability_id']!=program.ability_id):
            raise ValueError('Pending trigger usage has a different ability identity')
        if (claim['object_id']==source.object_id and claim['logical_object_id']==source.logical_object_id
                and claim['program_key']==program.key and claim['ability_id']==program.ability_id
                and claim['turn_sequence']==state.turn_sequence):return None
    if current and not trigger_usage_available(live,ability_id=program.ability_id,turn_sequence=state.turn_sequence):return None
    return TriggerUsagePlan(live if current else None)
