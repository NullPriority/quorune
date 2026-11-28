"""Schedule and materialize a repeating watch from grouped committed damage."""

from collections.abc import Mapping

from ..compiler.direct_target import DirectPermanentTargetSpec
from ..continuous_effect_state import ResolutionEffectSource
from ..event_damage_source_model import EVENT_DAMAGE_SOURCE_CONTEXT, EventDamageSourceReference
from ..fixed_damage_set_model import FixedDamageSetSpec, PermanentDamageGroup, PlayerDamageGroup, PlayerDamageRelation
from ..object_predicate import ObjectQuerySpec
from ..received_damage_model import (
    RECEIVED_DAMAGE_CAPABILITY, RECEIVED_DAMAGE_EVENT, RECEIVED_DAMAGE_WATCH_FIELD,
    RECEIVED_DAMAGE_WATCH_LABEL, ReceivedDamageWatch,
)
from ..replacement.immutable import thaw_value
from .effect_shape_walkers import contains_nested_key
from .event_damage_sources import event_damage_source_information, event_damage_source_node_capabilities


WATCHED_DAMAGE_CAPABILITY = "trigger.delayed.received_damage_until_cleanup"
WATCHED_DAMAGE_TEMPLATE = "received-damage-watch-until-cleanup-v1"


def watched_damage_schedule():
    return {"op": "delayed_trigger", "controller": "$controller",
            RECEIVED_DAMAGE_WATCH_FIELD: {"schema_version": 1, "creature": "$target.0"}}


def watched_damage_target_schema():
    return DirectPermanentTargetSpec(types_any=("creature",)).to_target_schema()


def watched_damage_result(amount: int):
    spec = FixedDamageSetSpec((
        PermanentDamageGroup(ObjectQuerySpec(zones=("battlefield",), types_all=("creature",)), exclude_source=True),
        PlayerDamageGroup(PlayerDamageRelation.ALL),
    ))
    return {"op": "damage_fixed_set", "source": EventDamageSourceReference(RECEIVED_DAMAGE_EVENT).to_dict(),
            "amount": amount, "groups": spec.to_dict()["groups"]}


def watched_damage_node_capabilities(effects, target_schema):
    if (
        not isinstance(target_schema, Mapping) or type(target_schema.get("count")) is not int
        or thaw_value(target_schema) != watched_damage_target_schema() or len(effects) != 1
        or thaw_value(effects[0]) != watched_damage_schedule()
        or type(effects[0][RECEIVED_DAMAGE_WATCH_FIELD]["schema_version"]) is not int
    ):
        return ()
    damage = event_damage_source_node_capabilities(
        effects=(watched_damage_result(1),), target_schema=None, mechanic_ids=("cr-120-damage",),
    )
    if not damage:
        raise ValueError("The received damage result lacks its ordinary simultaneous damage authority")
    return (WATCHED_DAMAGE_CAPABILITY, RECEIVED_DAMAGE_CAPABILITY, "trigger.placement.apnap",
            "target.revalidate_resolution", *damage)


def watched_damage_runtime_dependencies(program):
    if not contains_nested_key((program.effects, program.handlers), RECEIVED_DAMAGE_WATCH_FIELD):
        return ()
    required = watched_damage_node_capabilities(program.effects, program.target_schema)
    if (
        not required or (program.event, program.active_zone) != ("resolve", "stack")
        or not program.ability_id.startswith("spell:") or program.cost_schema is not None
        or program.event_condition is not None or program.handlers
    ):
        raise ValueError("A received damage watch requires its exact creature-targeted spell")
    return required


def schedule_watched_damage(host, effect, *, actor):
    from ..trigger_processing import schedule_delayed_trigger

    source = ResolutionEffectSource.from_effect(effect)
    spec = effect[RECEIVED_DAMAGE_WATCH_FIELD]
    if (
        effect.get("controller") != actor or not isinstance(spec, Mapping)
        or set(spec) != {"schema_version", "creature"} or type(spec["schema_version"]) is not int
        or spec["schema_version"] != 1 or type(spec["creature"]) is not str or not spec["creature"]
    ):
        raise ValueError("A resolving received damage watch requires its controller and exact creature")
    item = next((item for item in host.state.stack if item.ref == source.stack_ref), None)
    program = host.semantics.get(item.semantic_key) if item is not None and item.semantic_key else None
    if (
        program is None or program.trust_level != "trusted" or not watched_damage_runtime_dependencies(program)
        or item.kind not in {"spell", "spell_copy"} or item.controller != actor
        or item.context.get("currently_resolving") is not True or item.targets != [spec["creature"]]
    ):
        raise ValueError("A received damage watch lost its original resolving spell")
    target = host._resolve_object(actor, spec["creature"])
    if target.zone != "battlefield" or target.phased_out:
        raise ValueError("A received damage watch lost its original battlefield creature")
    watch = ReceivedDamageWatch(target.object_id, target.logical_object_id, source.stack_ref)
    return schedule_delayed_trigger(
        host, controller=actor, label=RECEIVED_DAMAGE_WATCH_LABEL, event_kind=RECEIVED_DAMAGE_EVENT,
        condition=watch.condition, stack_template=watch.stack_template, source_object_id=source.object_id,
        source_logical_object_id=source.logical_object_id, referred_object_ids=(watch.object_id,),
        once=False, expires_turn_sequence=host.state.turn_sequence,
    ).ref


def bind_watched_damage_occurrence(host, trigger, item, context):
    watch = ReceivedDamageWatch.from_trigger(trigger)
    if not isinstance(context, Mapping) or any(context.get(key) != value for key, value in watch.condition.items()):
        raise ValueError("A received damage watch requires its actual committed recipient occurrence")
    reference = EventDamageSourceReference(RECEIVED_DAMAGE_EVENT)
    info = event_damage_source_information(host, reference, RECEIVED_DAMAGE_EVENT, context)
    item.context.update(context)
    item.context.update({
        EVENT_DAMAGE_SOURCE_CONTEXT: info.to_dict(),
        "dynamic_effects": [watched_damage_result(info.received_amount)],
        "received_damage_watch_source_stack": watch.source_stack_ref,
    })
