"""Capture and resolve through the existing delayed-trigger and effect owners."""
from collections.abc import Mapping
from dataclasses import replace

from .continuous_effect_model import ContinuousObjectIdentity
from .delayed_object_model import (
    CONTROLLED_ATTACKER_PHASE_OUT_VERSION, DELAYED_SOURCE_CONVERT_VERSION, GRAVEYARD_COUNTER_RETURN_VERSION, GRAVEYARD_RETURN_VERSION,
    OBJECT_TIMINGS, RESOLVE_OBJECT_ACTION, SCHEDULE_OBJECT_ACTION,
    TARGET_DEATH_RETURN_CONDITION, TARGET_DEATH_RETURN_EVENT, TARGET_DEATH_RETURN_GRAVEYARD_OWNER,
    is_target_death_battlefield_return, is_target_death_return_effect,
    target_death_return_arrivals, target_death_return_resolution_effect, uses_source_death_hand_return,
    validate_delayed_object_instruction,
)
from .discard_effect_origin import OPPONENT_SOURCE_DISCARD_EVENT
from .errors import GameRuleError
from .event_counter_reference import BLOCK_COUNTERPART_CONTEXT, EVENT_COUNTER_CONTEXT
from .damage_recipient_model import DAMAGE_PERMANENT_OBJECT,DAMAGE_PERMANENT_CONTEXT
from .target_source import TARGETING_SOURCE_CONTEXT,TargetingSourceBinding
from .trigger_processing import TriggerProcessingOwner, schedule_delayed_trigger


_TARGET_DEATH_RETURN_LABEL = "Return the card to its owner's hand"


def _bind_target_death_return(host, effect, item):
    from .semantic_runtime.values import resolve_semantic_value

    target_ref = resolve_semantic_value(host, effect["object"], item)
    target = host._resolve_object(item.controller, target_ref, zones={"battlefield"})
    binding = ContinuousObjectIdentity(target.object_id, target.logical_object_id)
    return {**effect, "object": binding.to_dict()}


def _target_death_return_item(host, effect, actor):
    from .continuous_effect_state import ResolutionEffectSource

    try:
        source = ResolutionEffectSource.from_effect(effect)
    except ValueError as exc:
        raise GameRuleError(str(exc)) from exc
    item = next((item for item in host.state.stack if item.ref == source.stack_ref), None)
    if item is None or item.controller != actor or item.context.get("currently_resolving") is not True:
        raise GameRuleError("Death return requires its original resolving ability")
    if source.object_id is not None and (
            item.source_object_id != source.object_id
            or item.context.get("source_logical_object_id") != source.logical_object_id):
        raise GameRuleError("Death return cannot change its original ability source")
    return item


def _schedule_target_death_return(host, effect, instruction, actor):
    from .delayed_object_model import target_death_return_effect
    from .replacement.immutable import FrozenMap

    item = _target_death_return_item(host, effect, actor)
    battlefield = is_target_death_battlefield_return(instruction)
    compiled = target_death_return_effect(battlefield=battlefield)
    program = host.semantics.get(item.semantic_key)
    if (item.kind != "activated_ability" or program is None or tuple(program.effects) != (compiled,)
            or FrozenMap(instruction) != FrozenMap(_bind_target_death_return(host, compiled, item))):
        raise GameRuleError("Death return requires its original live activation target")
    target = ContinuousObjectIdentity.from_dict(instruction["object"])
    source = ContinuousObjectIdentity(item.source_object_id, item.context.get("source_logical_object_id"))
    condition = {TARGET_DEATH_RETURN_CONDITION: target.to_dict()}
    label = _TARGET_DEATH_RETURN_LABEL
    if battlefield:
        condition[TARGET_DEATH_RETURN_GRAVEYARD_OWNER] = actor
        label = "Return the card to the battlefield"
    return schedule_delayed_trigger(
        host, controller=actor, label=label, event_kind=TARGET_DEATH_RETURN_EVENT,
        condition=condition, stack_template={},
        source_object_id=source.object_id, source_logical_object_id=source.logical_object_id,
        referred_object_ids=(target.object_id,), once=True, expires_turn_sequence=host.state.turn_sequence,
    ).ref


def collect_target_graveyard_triggers(host, occurrence, composition_departures, pending) -> None:
    """Collect delayed results that watch the departing object incarnation."""
    if not getattr(getattr(host, "state", None), "delayed_triggers", ()):
        return
    from .rules.watched_death_counter_draw import collect_watched_death_counter_draw

    collect_target_death_return(host, occurrence, composition_departures, pending)
    collect_watched_death_counter_draw(host, occurrence, pending)


def collect_target_death_return(host, occurrence, composition_departures, pending) -> None:
    """Seal the physical arrivals of one committed battlefield-to-graveyard occurrence."""
    if not getattr(getattr(host, "state", None), "delayed_triggers", ()):
        return
    binding = ContinuousObjectIdentity(occurrence.object_id, occurrence.previous_logical_object_id)
    owner = TriggerProcessingOwner(host)
    matches = owner.matching_delayed_triggers(
        TARGET_DEATH_RETURN_EVENT, {TARGET_DEATH_RETURN_CONDITION: binding.to_dict(),
                                    TARGET_DEATH_RETURN_GRAVEYARD_OWNER: occurrence.owner},
    )
    if not matches:
        return
    composition = occurrence.composition
    departures = (occurrence,) if composition is None else composition_departures
    if composition is not None and (
            not composition.permanent_departure or len(departures) != composition.member_count
            or any(part.composition is None or not part.composition.physical_arrival
                   or part.composition.parent != composition.parent for part in departures)):
        raise GameRuleError("Death return requires the original physical departure facts")
    for trigger in matches:
        battlefield = TARGET_DEATH_RETURN_GRAVEYARD_OWNER in trigger.condition
        arrivals = tuple(
            ContinuousObjectIdentity(part.object_id, part.current_logical_object_id)
            for part in departures if part.destination == "graveyard" and part.card_object and not part.token
            and (not battlefield or part.owner == trigger.controller)
        )
        effect = target_death_return_resolution_effect(arrivals, battlefield=battlefield)
        pending.append(owner.materialize_delayed_trigger(replace(
            trigger, stack_template={"context": {"dynamic_effects": [effect]}},
            referred_object_ids=[arrival.object_id for arrival in arrivals],
        )))


def _resolve_target_death_return(host, effect, instruction, actor, reason):
    from .public_zone_moves import PublicBattlefieldController
    from .replacement.immutable import FrozenMap
    from .semantic_runtime.intents import MoveObjectsSimultaneouslyIntent

    item = _target_death_return_item(host, effect, actor)
    original = item.context.get("dynamic_effects", ())
    arrivals = target_death_return_arrivals(instruction["object"])
    battlefield = is_target_death_battlefield_return(instruction)
    expected = target_death_return_resolution_effect(arrivals, battlefield=battlefield)
    if (item.kind != "triggered_ability" or item.context.get("event") != TARGET_DEATH_RETURN_EVENT
            or not item.context.get("delayed_trigger_ref")
            or not isinstance(original, (list, tuple)) or len(original) != 1
            or not isinstance(original[0], Mapping)
            or FrozenMap(original[0]) != FrozenMap(expected)):
        raise GameRuleError("Death return requires its original delayed graveyard occurrence")
    references = []
    for arrival in arrivals:
        card = host.state.cards.get(arrival.object_id)
        if (card is not None and card.zone == "graveyard" and card.logical_object_id == arrival.logical_object_id
                and card.is_card_object and not card.is_token and (not battlefield or card.owner == actor)):
            references.append(card.ref)
    if not references:
        return ()
    return host.move_objects_simultaneously_intent(MoveObjectsSimultaneouslyIntent(
        actor=actor, object_refs=tuple(references), expected_zones=("graveyard",),
        destination="battlefield" if battlefield else "hand",
        battlefield_controller=PublicBattlefieldController.OWNER if battlefield else None,
        reason=reason, replacement_selections=tuple(instruction.get("_replacement_selections", ())),
    ))


def bind_delayed_object_instruction(host,effect,item):
    validate_delayed_object_instruction(effect,compiled=True)
    if uses_source_death_hand_return(effect):
        from .rules.source_death_delayed_hand_return import bind_source_death_hand_return

        return bind_source_death_hand_return(effect, item)
    from .delayed_object_model import is_artifact_graveyard_return
    if is_artifact_graveyard_return(effect):
        return _bind_artifact_graveyard_return(host, effect, item)
    if is_target_death_return_effect(effect):
        return _bind_target_death_return(host, effect, item)
    from .delayed_object_model import is_source_bound_creature_return
    if is_source_bound_creature_return(effect):
        from .rules.source_bound_delayed_return import bind_source_bound_return

        return bind_source_bound_return(effect, item)
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
    if effect["schema_version"] == DELAYED_SOURCE_CONVERT_VERSION:
        source = host.state.cards.get(item.source_object_id)
        original = obj.get("logical_object_id")
        current = source is not None and source.zone == "battlefield" and source.logical_object_id == original
        result["expected_transform_count"] = source.transform_count if current else None
        if not obj.get("reference") or not original:
            result["object"] = None
    if effect['schema_version']==2:
        reference=host._stack_source_ref(item)
        raw=item.context.get(TARGETING_SOURCE_CONTEXT)
        if reference is None and isinstance(raw,Mapping):reference=TargetingSourceBinding.from_dict(raw).object_ref
        result['source']=reference
    validate_delayed_object_instruction(result)
    return result


def apply_delayed_object_action(host,effect,*,actor,operation,reason):
    instruction={key:value for key,value in effect.items() if key not in {'reason','_runtime_source'}}
    from .delayed_object_model import is_source_bound_creature_return
    if is_source_bound_creature_return(instruction):
        from .rules.source_bound_delayed_return import apply_source_bound_return

        return apply_source_bound_return(host, instruction, runtime_source=effect.get("_runtime_source"),
                                         actor=actor, reason=reason)
    try:validate_delayed_object_instruction(instruction)
    except (ValueError,TypeError,KeyError) as exc:raise GameRuleError(str(exc)) from exc
    if instruction['op']!=operation:raise GameRuleError('Delayed action operation changed')
    if uses_source_death_hand_return(instruction):
        from .rules.source_death_delayed_hand_return import apply_source_death_hand_return

        return apply_source_death_hand_return(host, effect, instruction, actor=actor, reason=reason)
    from .delayed_object_model import is_artifact_graveyard_return
    if is_artifact_graveyard_return(instruction):
        if operation == SCHEDULE_OBJECT_ACTION:
            return _schedule_artifact_graveyard_return(host, effect, instruction, actor)
        return _resolve_artifact_graveyard_return(host, effect, instruction, actor, reason)
    if is_target_death_return_effect(instruction):
        if operation == SCHEDULE_OBJECT_ACTION:
            return _schedule_target_death_return(host, effect, instruction, actor)
        return _resolve_target_death_return(host, effect, instruction, actor, reason)
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
            obj = instruction["object"]
            future.update(op=RESOLVE_OBJECT_ACTION, object=dict(obj) if obj is not None else None)
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
    if instruction["schema_version"] == DELAYED_SOURCE_CONVERT_VERSION:
        from .permanent_transform import commit_transform_batch

        count = instruction["expected_transform_count"]
        if count is None:
            return None
        results = commit_transform_batch(
            host, (card,), reason=reason, day_night_instruction=False,
            expected_transform_counts={card.object_id: count}, conversion=True,
        )
        return results[0].card_ref if results else None
    action={'op':instruction['action'],'card':card.ref,'reason':reason}
    if instruction['schema_version']==2:action.update({key:instruction[key] for key in ('counter','amount','source')})
    return host.apply_effect(action,actor=actor)


def capture_artifact_graveyard_composition(host, context, occurrence, parts):
    from .delayed_object_model import (
        ARTIFACT_GRAVEYARD_COMPOSITION_FIELD, ARTIFACT_GRAVEYARD_RETURN_EVENT, is_artifact_graveyard_return,
    )
    from .rules.death_battlefield_return import committed_graveyard_arrivals

    if any(any(is_artifact_graveyard_return(effect) for effect in program.effects)
           for program in host.semantics.programs_for_event(ARTIFACT_GRAVEYARD_RETURN_EVENT)):
        return {**context, ARTIFACT_GRAVEYARD_COMPOSITION_FIELD: committed_graveyard_arrivals(occurrence, parts)}
    return context


def filter_graveyard_receipt_context(event, context, program):
    if event == "creature.dies":
        from .event_card_return import (
            DEATH_EXILE_ARRIVALS,
            DEATH_EXILE_ARRIVAL_CAPABILITIES,
            EVENT_LIBRARY_COMPONENTS,
            EVENT_LIBRARY_COMPOSITION_CAPABILITY,
        )

        if DEATH_EXILE_ARRIVAL_CAPABILITIES.isdisjoint(program.capability_dependencies):
            context.pop(DEATH_EXILE_ARRIVALS, None)
        if EVENT_LIBRARY_COMPOSITION_CAPABILITY not in program.capability_dependencies:
            context.pop(EVENT_LIBRARY_COMPONENTS, None)
    elif event == "artifact.graveyard":
        from .delayed_object_model import ARTIFACT_GRAVEYARD_COMPOSITION_FIELD, is_artifact_graveyard_return

        if not any(is_artifact_graveyard_return(effect) for effect in program.effects):
            context.pop(ARTIFACT_GRAVEYARD_COMPOSITION_FIELD, None)

    if event == "creature.dies":
        from .rules.death_battlefield_return import DEATH_EQUIPMENT_ARRIVALS, DEATH_EQUIPMENT_RETURN_CAPABILITY

        if DEATH_EQUIPMENT_RETURN_CAPABILITY not in program.capability_dependencies:
            context.pop(DEATH_EQUIPMENT_ARRIVALS, None)


def _bind_artifact_graveyard_return(host, effect, item):
    from .delayed_object_model import ARTIFACT_GRAVEYARD_COMPOSITION_FIELD, ARTIFACT_GRAVEYARD_RETURN_EVENT
    from .rules.death_battlefield_return import DeathBattlefieldArrival, decode_death_battlefield_arrivals

    source = host.state.cards.get(item.source_object_id)
    facts = item.context
    types = facts.get("types")
    if (source is None or source.object_kind != "emblem" or source.zone != "command"
            or source.logical_object_id != facts.get("source_logical_object_id")
            or facts.get("event") != ARTIFACT_GRAVEYARD_RETURN_EVENT or facts.get("owner") != source.controller
            or (facts.get("from"), facts.get("to")) != ("battlefield", "graveyard")
            or not isinstance(types, (list, tuple)) or "artifact" not in types or type(facts.get("token")) is not bool):
        raise ValueError("Delayed artifact return lost its original emblem and graveyard occurrence")
    if facts.get("composition") is not None:
        arrivals = decode_death_battlefield_arrivals(facts.get(ARTIFACT_GRAVEYARD_COMPOSITION_FIELD))
        if any(row.owner != facts["owner"] for row in arrivals):
            raise ValueError("Delayed artifact return requires the original graveyard owner's arrivals")
        return {**effect, "object": [row.to_dict() for row in arrivals]}
    if facts["token"]:
        return {**effect, "object": []}
    identity = facts.get("card_object_identity")
    object_id, separator, previous = identity.rpartition("@") if isinstance(identity, str) else ("", "", "")
    arrival = facts.get("card_zone_change_counter")
    if not separator or not previous.isascii() or not previous.isdecimal() or type(arrival) is not int or arrival != int(previous) + 1:
        raise ValueError("Delayed artifact return requires the original committed graveyard incarnation")
    row = DeathBattlefieldArrival(object_id, facts.get("card"), arrival, facts["owner"])
    return {**effect, "object": [row.to_dict()]}


def _schedule_artifact_graveyard_return(host, effect, instruction, actor):
    from .delayed_object_model import artifact_graveyard_return_effect
    from .replacement.immutable import FrozenMap, thaw_value
    from .rules.death_battlefield_return import decode_death_battlefield_arrivals

    item = _target_death_return_item(host, effect, actor)
    compiled = artifact_graveyard_return_effect()
    program = host.semantics.get(item.semantic_key)
    if (program is None or tuple(program.effects) != (compiled,)
            or FrozenMap(instruction) != FrozenMap(_bind_artifact_graveyard_return(host, compiled, item))):
        raise GameRuleError("Delayed artifact return requires its original resolving emblem ability")
    arrivals = decode_death_battlefield_arrivals(instruction["object"])
    if not arrivals:
        return None
    future = {**thaw_value(FrozenMap(instruction)), "op": RESOLVE_OBJECT_ACTION}
    future.pop("timing")
    label = "Return the artifact from the original graveyard occurrence"
    return schedule_delayed_trigger(host, controller=actor, label=label, event_kind="step.begin",
        condition=OBJECT_TIMINGS["next_end_step"], stack_template={"context": {"dynamic_effects": [future]}},
        source_object_id=item.source_object_id, source_logical_object_id=item.context["source_logical_object_id"],
        referred_object_ids=[arrival.object_id for arrival in arrivals], once=True).ref


def _resolve_artifact_graveyard_return(host, effect, instruction, actor, reason):
    from .characteristic_evaluation import type_parts
    from .external_entry_state_model import PERMANENT_CARD_TYPES
    from .replacement.immutable import FrozenMap
    from .rules.death_battlefield_return import decode_death_battlefield_arrivals
    from .zone_transitions import ZoneTransitionOwner

    item = _target_death_return_item(host, effect, actor)
    original = item.context.get("dynamic_effects", ())
    saved = {key: value for key, value in instruction.items() if key not in {
        "_replacement_selections", "_aura_target_refs",
    }}
    if (item.context.get("event") != "step.begin" or not item.context.get("delayed_trigger_ref")
            or not isinstance(original, (list, tuple)) or len(original) != 1
            or FrozenMap(original[0]) != FrozenMap(saved)):
        raise GameRuleError("Artifact return lost its original delayed instruction")
    arrivals = decode_death_battlefield_arrivals(instruction["object"])
    aura_targets = instruction.get("_aura_target_refs", {})
    if (not set(aura_targets) <= {row.object_id for row in arrivals}
            or any(type(ref) is not str or not ref for ref in aura_targets.values())):
        raise GameRuleError("Artifact return Aura choices require original graveyard arrivals")
    cards = tuple(card for row in arrivals if row.matches(card := host.state.cards.get(row.object_id))
                  and card.owner in host.active_seats
                  and type_parts(str(host._effective_card_data(card).get("type_line") or ""))[0] & PERMANENT_CARD_TYPES)
    if not cards:
        return ()
    changes = tuple((card.object_id, "battlefield") for card in cards)
    controllers = {card.object_id: actor for card in cards}
    moved = ZoneTransitionOwner(host).move_cards_simultaneously(
        changes, reason=reason, destination_controllers=controllers, putting_players=controllers,
        replacement_selections=tuple(instruction.get("_replacement_selections", ())), aura_target_refs=aura_targets,
    )
    return tuple(card.ref for card in moved if card.zone == "battlefield")
