"""CR 603.7 actions on an exact object incarnation in its required zone."""
from collections.abc import Mapping
from typing import Any
from .counter_names import normalized_counter_name
from .damage_recipient_model import DAMAGE_PERMANENT_OBJECT,DAMAGE_PERMANENT_CAPABILITY

DELAYED_OBJECT_CAPABILITY='trigger.delayed.fixed_object_action'
DELAYED_OBJECT_MECHANIC='fixed-delayed-object-action'
SCHEDULE_OBJECT_ACTION='schedule_fixed_object_action'
RESOLVE_OBJECT_ACTION='resolve_fixed_object_action'
OBJECT_ACTIONS=frozenset({'destroy','sacrifice','bounce'})
COUNTER_OBJECT_ACTIONS=frozenset({'place_counters','remove_counters'})
OBJECT_TIMINGS={'end_of_combat':{'phase':'combat','step':'end_combat'},
    'next_end_step':{'phase':'ending','step':'end_step'}}
OBJECT_REFERENCES=frozenset({'$source.zone_object','$event.block_counterpart',DAMAGE_PERMANENT_OBJECT})


FORCED_BLOCK_STATUS_VERSION = 4
GRAVEYARD_RETURN_VERSION = 5
GRAVEYARD_COUNTER_RETURN_VERSION = 6
CONTROLLED_ATTACKER_PHASE_OUT_VERSION = 7
_ACTIONS_BY_VERSION = {
    1: OBJECT_ACTIONS,
    2: COUNTER_OBJECT_ACTIONS,
    GRAVEYARD_RETURN_VERSION: frozenset({"bounce"}),
    GRAVEYARD_COUNTER_RETURN_VERSION: frozenset({"return_with_counter"}),
    CONTROLLED_ATTACKER_PHASE_OUT_VERSION: frozenset({"phase_out"}),
}
FORCED_BLOCK_STATUS_CAPABILITIES = ("combat.declaration.typed_components",
    "target.revalidate_resolution", "trigger.event.normalized_public_action")


def controlled_attacker_delayed_phase_out_effect():
    return {"op": SCHEDULE_OBJECT_ACTION, "schema_version": CONTROLLED_ATTACKER_PHASE_OUT_VERSION,
            "object": "$event.permanent_object", "action": "phase_out", "timing": "end_of_combat"}


def controlled_attacker_delayed_phase_out_capabilities():
    from .phasing_model import PHASE_OUT_CAPABILITY

    return (DELAYED_OBJECT_CAPABILITY, PHASE_OUT_CAPABILITY, "resolution.event_object.fixed_result",
            "trigger.event.normalized_public_action", "trigger.placement.apnap", "trigger.effect.fixed_event")


def forced_block_status_target_schema() -> dict[str, Any]:
    return {
        "zones": ["battlefield"], "categories": ["permanent"], "types_any": ["creature"],
        "combat_state": "unblocked_attacking", "count": 1,
    }


def forced_block_status_effect() -> dict[str, Any]:
    return {"op": RESOLVE_OBJECT_ACTION, "schema_version": FORCED_BLOCK_STATUS_VERSION,
            "action": "become_blocked", "object": "$target.0"}


def is_forced_block_status_effect(effect: Mapping[str, Any]) -> bool:
    return (isinstance(effect, Mapping) and effect.get("op") == RESOLVE_OBJECT_ACTION
            and (effect.get("schema_version") == FORCED_BLOCK_STATUS_VERSION or effect.get("action") == "become_blocked"))


def validate_forced_block_status_effect(effect: Mapping[str, Any], *, compiled: bool) -> None:
    expected = forced_block_status_effect()
    if (not isinstance(effect, Mapping) or set(effect) != set(expected)
            or type(effect["schema_version"]) is not int
            or {**effect, "object": "$target.0"} != expected):
        raise ValueError("A blocked-status effect requires one exact current attacker")
    value = effect["object"]
    if compiled and value == "$target.0" or not compiled and value is None:
        return
    if (compiled or not isinstance(value, Mapping) or set(value) != {"reference", "logical_object_id"}
            or any(type(text) is not str or not text or text.startswith("$") for text in value.values())):
        raise ValueError("A resolved blocked-status effect requires its exact attacking incarnation")


def validate_delayed_object_instruction(effect,*,compiled=False):
    if not isinstance(effect,Mapping):raise ValueError('Delayed object instruction must be a mapping')
    if is_forced_block_status_effect(effect):
        validate_forced_block_status_effect(effect, compiled=compiled)
        return effect
    op=effect.get('op');version=effect.get('schema_version');fields={'op','schema_version','object','action'}
    if type(version)is int and version==2:fields.update({'counter','amount','source'})
    if op==SCHEDULE_OBJECT_ACTION:fields.add('timing')
    if (op not in {SCHEDULE_OBJECT_ACTION,RESOLVE_OBJECT_ACTION} or set(effect)!=fields
            or type(version)is not int or version not in _ACTIONS_BY_VERSION
            or type(effect['action'])is not str or effect['action'] not in _ACTIONS_BY_VERSION[version]):
        raise ValueError('Delayed object action has a closed version and operation')
    if version == GRAVEYARD_COUNTER_RETURN_VERSION and (
            op != SCHEDULE_OBJECT_ACTION or effect["timing"] != "next_end_step"
            or (compiled and effect["object"] != "$source.zone_object")
    ):
        raise ValueError("Counter return requires the discarded source at the next end step")
    if version == GRAVEYARD_RETURN_VERSION and (
            effect["action"] != "bounce"
            or (op == SCHEDULE_OBJECT_ACTION and effect["timing"] != "next_end_step")
            or (compiled and effect["object"] != "$source.zone_object")
    ):
        raise ValueError("Graveyard return requires the original source at the next end step")
    if version == CONTROLLED_ATTACKER_PHASE_OUT_VERSION and (
            op == SCHEDULE_OBJECT_ACTION and effect["timing"] != "end_of_combat"
            or compiled and dict(effect) != controlled_attacker_delayed_phase_out_effect()
    ):
        raise ValueError("Delayed phasing requires the original attacker at end of combat")
    if version==2 and (type(effect['counter'])is not str or not effect['counter']
            or normalized_counter_name(effect['counter'])!=effect['counter']
            or type(effect['amount'])is not int or effect['amount']<=0
            or type(effect['source'])is not str or not effect['source']
            or compiled and (effect['object']!='$source.zone_object' or effect['source']!='$source')
            or not compiled and effect['source'].startswith('$')):
        raise ValueError('Delayed counters require one canonical fixed amount, name and creating source')
    if op==SCHEDULE_OBJECT_ACTION and (type(effect['timing'])is not str or effect['timing']not in OBJECT_TIMINGS):
        raise ValueError('Delayed object timing is outside the closed family')
    obj=effect['object']
    if compiled:
        references = ({"$event.permanent_object"} if version == CONTROLLED_ATTACKER_PHASE_OUT_VERSION
                      else OBJECT_REFERENCES)
        if op!=SCHEDULE_OBJECT_ACTION or type(obj)is not str or obj not in references:
            raise ValueError('Compiled delayed object must have one sealed source or counterpart reference')
    elif (not isinstance(obj,Mapping) or set(obj)!={'reference','logical_object_id'}
            or any(type(v)is not str or not v for v in obj.values())):
        raise ValueError('Delayed object needs its paired reference and incarnation')
    return effect


def delayed_object_node_capabilities(*,effects,target_schema,mechanic_ids):
    if DELAYED_OBJECT_MECHANIC not in mechanic_ids or target_schema is not None or len(effects)!=1:return ()
    try:effect=validate_delayed_object_instruction(effects[0],compiled=True)
    except (ValueError,TypeError,KeyError):return ()
    if effect["schema_version"] == CONTROLLED_ATTACKER_PHASE_OUT_VERSION:
        return controlled_attacker_delayed_phase_out_capabilities()
    if effect['schema_version']!=1:return ()
    action_caps={'destroy':'permanent.destroy.effect','sacrifice':'choice.affected_player.fixed_sacrifice',
        'bounce':'permanent.return.owner_hand'}
    return (DELAYED_OBJECT_CAPABILITY,'trigger.placement.apnap','zone.change.destination_replacement',action_caps[effect['action']],
        *((DAMAGE_PERMANENT_CAPABILITY,) if effect['object']==DAMAGE_PERMANENT_OBJECT else ()))
