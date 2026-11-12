"""Effect-generated counters on one pinned graveyard-to-battlefield move."""
from collections.abc import Mapping
from .entry_counter_model import EffectEntryCounter, validate_required_entry_type
from .keyword_counters import KEYWORD_COUNTER_MECHANICS
from .semantic_runtime.intents import ZoneMoveIntent

REANIMATION_ENTRY_COUNTER_CAPABILITY='zone.reanimate.entry_counters'
REANIMATION_ENTRY_COUNTER_MECHANIC='fixed-target-entry-counter-reanimation'
CONDITIONAL_REANIMATION_COUNTER_CAPABILITY = "zone.reanimate.conditional_entry_counters"
CONDITIONAL_REANIMATION_COUNTER_MECHANIC = "conditional-target-entry-counter-reanimation"
CONDITIONAL_REANIMATION_COUNTER_VERSION = 6
REANIMATION_ENTRY_COUNTER_VERSIONS = (3, CONDITIONAL_REANIMATION_COUNTER_VERSION)
_FIELDS={'op','schema_version','card','controller','tapped','entry_counter','source'}


def fixed_reanimation_entry_counter(value):
    if not isinstance(value,Mapping) or set(value)!={'counter_name','amount'}:
        raise ValueError('Reanimation entry counter fields are incomplete or unknown')
    name,amount=value['counter_name'],value['amount']
    if (type(name)is not str or name not in {'+1/+1','-1/-1','finality',*KEYWORD_COUNTER_MECHANICS}
            or type(amount)is not int or not 1<=amount<=10):
        raise ValueError('Reanimation requires one closed fixed counter and amount')
    return name,amount


def _reanimation_entry_counter(effect):
    counter = effect["entry_counter"]
    if effect["schema_version"] == 3:
        return (*fixed_reanimation_entry_counter(counter), None)
    if not isinstance(counter, Mapping) or set(counter) != {"counter_name", "amount", "required_entry_type"}:
        raise ValueError("Conditional reanimation requires its exact entry counter fields")
    name, amount = fixed_reanimation_entry_counter({
        "counter_name": counter["counter_name"], "amount": counter["amount"],
    })
    return name, amount, validate_required_entry_type(counter["required_entry_type"])


def validate_reanimation_entry_effect(effect,*,materialized=False):
    expected=_FIELDS|({'target_snapshot'} if materialized else set())
    allowed=expected|({'_replacement_selections'} if materialized else set())
    if (not isinstance(effect,Mapping) or not expected<=set(effect)<=allowed
            or effect.get('op')!='reanimate' or type(effect.get('schema_version'))is not int
            or effect['schema_version'] not in REANIMATION_ENTRY_COUNTER_VERSIONS or type(effect.get('tapped'))is not bool):
        raise ValueError('Entry-counter reanimation requires its complete versioned shape')
    _reanimation_entry_counter(effect)
    if not materialized:
        if effect['card']!='$target.0' or effect['controller'] not in {'$controller','$target.owner.0'} or effect['source']!='$stack':
            raise ValueError('Entry-counter reanimation requires its bound target, controller and stack source')
        return
    if any(type(effect[k])is not str or not effect[k] for k in ('card','controller','source')):
        raise ValueError('Entry-counter reanimation identities are unavailable')
    snapshot=effect['target_snapshot']
    if (not isinstance(snapshot,Mapping) or set(snapshot)!={'ref','object_id','zone_change_counter'}
            or snapshot['ref']!=effect['card'] or type(snapshot['object_id'])is not str or not snapshot['object_id']
            or type(snapshot['zone_change_counter'])is not int or snapshot['zone_change_counter']<0):
        raise ValueError('Entry-counter reanimation requires its original target incarnation')
    if not isinstance(effect.get('_replacement_selections',()),(list,tuple)):
        raise ValueError('Reanimation replacement selections must be a sequence')


def reanimation_entry_counter_intent(effect,*,actor,reason):
    validate_reanimation_entry_effect(effect,materialized=True)
    name, amount, required_type = _reanimation_entry_counter(effect)
    counters = (EffectEntryCounter(
        name, amount, effect["controller"], effect["source"], "122.6a",
        required_entry_type=required_type,
    ),)
    return ZoneMoveIntent(actor=actor,object_ref=effect['card'],expected_zones=('graveyard',),
        destination='battlefield',reason=reason,new_controller=effect['controller'],
        tapped_policy='tapped' if effect['tapped'] else 'untapped',optional_if_missing=True,
        expected_zone_change_counter=effect['target_snapshot']['zone_change_counter'],
        effect_entry_counters=counters,replacement_selections=tuple(effect.get('_replacement_selections',())))
