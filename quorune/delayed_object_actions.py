"""Capture and resolve through the existing delayed-trigger and effect owners."""
from collections.abc import Mapping
from .delayed_object_model import (
    CONTROLLED_ATTACKER_PHASE_OUT_VERSION, GRAVEYARD_COUNTER_RETURN_VERSION, GRAVEYARD_RETURN_VERSION,
    OBJECT_TIMINGS, RESOLVE_OBJECT_ACTION, SCHEDULE_OBJECT_ACTION, validate_delayed_object_instruction,
)
from .discard_effect_origin import OPPONENT_SOURCE_DISCARD_EVENT
from .errors import GameRuleError
from .event_counter_reference import BLOCK_COUNTERPART_CONTEXT, EVENT_COUNTER_CONTEXT
from .damage_recipient_model import DAMAGE_PERMANENT_OBJECT,DAMAGE_PERMANENT_CONTEXT
from .target_source import TARGETING_SOURCE_CONTEXT,TargetingSourceBinding
from .trigger_processing import schedule_delayed_trigger


def bind_delayed_object_instruction(host,effect,item):
    validate_delayed_object_instruction(effect,compiled=True)
    if effect["schema_version"] in {GRAVEYARD_RETURN_VERSION, GRAVEYARD_COUNTER_RETURN_VERSION}:
        identity = item.context.get("discarded_logical_object_id")
        if (
            item.context.get("event") != OPPONENT_SOURCE_DISCARD_EVENT
            or not identity
            or identity != item.context.get("source_logical_object_id")
        ):
            raise ValueError("Graveyard return requires the committed discarded incarnation")
        obj = {"reference": item.context.get("card"), "logical_object_id": identity}
    elif effect["schema_version"] == CONTROLLED_ATTACKER_PHASE_OUT_VERSION:
        if item.context.get("event") != "creature.attacks":
            raise ValueError("Delayed phasing requires its original attack occurrence")
        obj = item.context.get(EVENT_COUNTER_CONTEXT)
    elif effect['object']=='$event.block_counterpart':obj=item.context.get(BLOCK_COUNTERPART_CONTEXT)
    elif effect['object']==DAMAGE_PERMANENT_OBJECT:obj=item.context.get(DAMAGE_PERMANENT_CONTEXT)
    else:
        reference=host._stack_source_ref(item)
        identity=item.context.get('source_logical_object_id')
        raw=item.context.get(TARGETING_SOURCE_CONTEXT)
        if reference is None and isinstance(raw,Mapping):reference=TargetingSourceBinding.from_dict(raw).object_ref
        obj={'reference':reference,'logical_object_id':identity}
    result={**effect,'object':dict(obj) if isinstance(obj,Mapping) else obj}
    if effect['schema_version']==2:
        reference=host._stack_source_ref(item)
        raw=item.context.get(TARGETING_SOURCE_CONTEXT)
        if reference is None and isinstance(raw,Mapping):reference=TargetingSourceBinding.from_dict(raw).object_ref
        result['source']=reference
    validate_delayed_object_instruction(result)
    return result


def apply_delayed_object_action(host,effect,*,actor,operation,reason):
    instruction={key:value for key,value in effect.items() if key not in {'reason','_runtime_source'}}
    try:validate_delayed_object_instruction(instruction)
    except (ValueError,TypeError,KeyError) as exc:raise GameRuleError(str(exc)) from exc
    if instruction['op']!=operation:raise GameRuleError('Delayed action operation changed')
    if operation==SCHEDULE_OBJECT_ACTION:
        source=effect.get('_runtime_source')
        if not isinstance(source,Mapping):raise GameRuleError('Delayed object scheduling requires its creating stack source')
        if instruction["schema_version"] == GRAVEYARD_COUNTER_RETURN_VERSION:
            obj = instruction["object"]
            object_id, separator, counter = obj["logical_object_id"].rpartition("@")
            if (not separator or object_id != source["object_id"] or not counter.isascii()
                    or not counter.isdecimal() or int(counter) < 1
                    or obj["logical_object_id"] != source["logical_object_id"]):
                raise GameRuleError("Delayed entry requires the original discarded card incarnation")
            future = {
                "op": "reanimate", "schema_version": 2, "card": obj["reference"],
                "expected_zone_change_counter": int(counter), "controller": actor, "tapped": False,
                "entry_counter": "+1/+1", "source": "$stack",
            }
            label = "Return discarded card with a counter"
        else:
            future = {key: value for key, value in instruction.items() if key not in {"op", "timing"}}
            future.update(op=RESOLVE_OBJECT_ACTION, object=dict(instruction["object"]))
            if instruction["schema_version"] == 2:
                label = "Delayed counter placement" if instruction["action"] == "place_counters" else "Delayed counter removal"
            elif instruction["schema_version"] == CONTROLLED_ATTACKER_PHASE_OUT_VERSION:
                label = "Phase out the original attacker"
            else:
                label = "Delayed " + instruction["action"]
        return schedule_delayed_trigger(host,controller=actor,label=label,
            event_kind='step.begin',condition=OBJECT_TIMINGS[instruction['timing']],
            stack_template={'label':label,'context':{'dynamic_effects':[future]}},
            source_object_id=source['object_id'],source_logical_object_id=source['logical_object_id'],once=True).ref
    obj = instruction["object"]
    if obj is None:
        return None
    card = next((c for c in host.state.cards.values() if c.ref == obj["reference"]), None)
    from .delayed_object_model import is_forced_block_status_effect
    if is_forced_block_status_effect(instruction):
        from .block_transition_engine_adapter import commit_engine_blocked_status

        if card is not None and card.logical_object_id == obj["logical_object_id"]:
            return card.ref if commit_engine_blocked_status(host, card) else None
        return None
    if instruction["schema_version"] == GRAVEYARD_RETURN_VERSION:
        if card is None or card.zone != "graveyard" or card.logical_object_id != obj["logical_object_id"]:
            return None
        return host.apply_effect(
            {"op": "move_if_in_zone", "card": card.ref, "from": "graveyard", "destination": "hand",
             "expected_object_identity": obj["logical_object_id"], "reason": reason},
            actor=actor,
        )
    if (card is None or card.zone!='battlefield' or card.phased_out
            or card.logical_object_id!=obj['logical_object_id']
            or instruction['action']=='sacrifice' and card.controller!=actor):return None
    if instruction["schema_version"] == CONTROLLED_ATTACKER_PHASE_OUT_VERSION:
        from .continuous_effect_model import ContinuousObjectIdentity
        from .phasing import phase_out_permanent

        return phase_out_permanent(host, ContinuousObjectIdentity(card.object_id, card.logical_object_id),
                                   actor=actor, reason=reason)
    action={'op':instruction['action'],'card':card.ref,'reason':reason}
    if instruction['schema_version']==2:action.update({key:instruction[key] for key in ('counter','amount','source')})
    return host.apply_effect(action,actor=actor)
