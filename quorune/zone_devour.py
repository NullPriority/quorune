from __future__ import annotations

"""Validate and expand Devour plans for the canonical simultaneous zone owner."""

from collections.abc import Mapping

from .devour import (DEVOUR_CAPABILITY, DEVOUR_ENTRY_RECEIPT_FIELD, DEVOUR_ENTRY_RESULT_CAPABILITY,
                     DEVOUR_ENTRY_RESULT_KIND, DevourEntryAmount, DevourEntryReceipt,
                     DevourProposal, DevourQuality)
from .errors import GameRuleError
from .zone_trigger_events import ZoneTransitionKind


def expand_devour_zone_plans(host,changes,prepared,kinds):
    if not any(prepared[oid].devour_sacrifices for oid,_ in changes):return tuple(changes),prepared,kinds
    expanded=list(changes);plans=dict(prepared);transitions=dict(kinds);reserved={oid for oid,_ in changes}
    for oid,_ in changes:
        parent=prepared[oid];linked={p.object_id:p for p in parent.devour_sacrifices}
        for raw in parent.event.payload.get('devour_proposals',()):
            proposal=DevourProposal.from_dict(raw)
            for selected in proposal.objects:
                card=host.state.cards.get(selected.object_id)
                if (card is None or card.zone!='battlefield' or card.phased_out or card.controller!=proposal.controller
                        or card.logical_object_id!=selected.logical_object_id or card.ref!=selected.ref or card.object_id in reserved):
                    raise GameRuleError('A Devour sacrifice changed after its captured choice')
                data=host._effective_card_data(card);types,subtypes,_=host._type_parts(str(data.get('type_line') or ''))
                quality=proposal.spec.quality
                if (quality is DevourQuality.CREATURE and 'creature' not in types
                        or quality is DevourQuality.ARTIFACT and 'artifact' not in types
                        or quality is DevourQuality.FOOD and not ('artifact' in types and 'food' in subtypes)):
                    raise GameRuleError('A Devour sacrifice no longer matches its quality')
                plan=linked[selected.object_id]
                expanded.append((card.object_id,plan.requested_destination));plans[card.object_id]=plan
                transitions[card.object_id]=ZoneTransitionKind.SACRIFICE;reserved.add(card.object_id)
    return tuple(expanded),plans,transitions


def commit_single_devour_entry(owner,plan,*,reason,log,aura_target_ref,semantic_events,putting_player=None):
    card=plan.card;prepared=plan.prepared_replacement
    owner.move_cards_simultaneously(((card.object_id,plan.requested_destination),),reason=reason,log=log,
        destination_controllers={card.object_id:prepared.destination_controller} if plan.requested_destination=='battlefield' else {},
        aura_target_refs={card.object_id:aura_target_ref} if aura_target_ref is not None else {},
        transition_kinds={card.object_id:prepared.transition_kind},prepared_replacements={card.object_id:prepared},
        entry_faces={card.object_id:plan.enter_face} if plan.enter_face is not None else {},
        publish_events=semantic_events,
        putting_players={card.object_id: putting_player} if putting_player is not None else None)
    return owner.state.cards[card.object_id]


def prepare_token_devour_sacrifices(host,entries,*,reason):
    """Prepare the zone-owned half before the token owner allocates anything."""
    linked=tuple(p for entry in entries.values() for p in (*entry.devour_sacrifices,*entry.graveyard_copy_exiles))
    if not linked:return None
    roots=tuple((oid,entry.requested_destination) for oid,entry in entries.items())
    from .rules.graveyard_entry_copy import expand_graveyard_copy_zone_plans
    changes,prepared,kinds=expand_devour_zone_plans(host,roots,dict(entries),{})
    changes,prepared,kinds=expand_graveyard_copy_zone_plans(host,changes,prepared,kinds)
    changes=tuple((oid,destination) for oid,destination in changes if oid not in entries)
    prepared={oid:prepared[oid] for oid,_ in changes}
    from .zone_transitions import ZoneTransitionOwner
    owner=ZoneTransitionOwner(host)
    changes,prepared,kinds,sources=owner._prepare_simultaneous_zone_batch(changes,{}, {}, {},tapped=None,
        effect_entry_counters=None,replacement_selections=(),kinds=kinds,prepared_replacements=prepared,log=False)
    return owner,changes,prepared,kinds,sources


def commit_token_devour_sacrifices(prepared,*,timestamp,reason):
    if prepared is None:return None
    owner,changes,plans,kinds,sources=prepared
    snapshots=owner._commit_simultaneous_zone_batch(changes,plans,kinds,{}, {}, {},sources,
        reason=reason,log=False,destination_timestamp=timestamp)
    return owner,snapshots,plans,kinds


def publish_token_devour_sacrifices(committed,*,reason):
    if committed is None:return
    owner,snapshots,plans,kinds=committed
    owner._publish_simultaneous_zone_batch(snapshots,plans,kinds,reason=reason)


def validate_devour_resume(host,batch,effects,selection,*,error_type):
    """A saved choice keeps captured identities instead of refreshing authority."""
    from .replacement.operations import DevourEntryOperation, ENTRY_OBJECT_SELECTION_OPERATIONS
    selected=[]
    if isinstance(selection,dict) and selection.get('objects'):
        effect=next((e for e in effects if e.effect_id==selection['effect_id']),None)
        operations=tuple(op for op in effect.operations if isinstance(op,ENTRY_OBJECT_SELECTION_OPERATIONS)) if effect is not None else ()
        if len(operations)!=1:raise error_type('Selected replacement has no entry object identity contract')
        operation=operations[0]
        if isinstance(operation, DevourEntryOperation):
            candidates={obj.ref:obj for obj in operation.candidates}
            for ref in selection['objects']:
                if ref not in candidates:raise error_type('Selected Devour object was not captured')
                selected.append((operation.controller,operation.spec,candidates[ref]))
    for event in batch.events:
        for raw in event.payload.get('devour_proposals',()):
            proposal=DevourProposal.from_dict(raw)
            selected.extend((proposal.controller,proposal.spec,obj) for obj in proposal.objects)
    for controller,spec,obj in selected:
        card=host.state.cards.get(obj.object_id)
        if (card is None or card.zone!='battlefield' or card.phased_out or card.controller!=controller
                or card.logical_object_id!=obj.logical_object_id or card.ref!=obj.ref):
            raise error_type('Devour selected incarnation changed before replacement resume')
        data=host._effective_card_data(card);types,subtypes,_=host._type_parts(str(data.get('type_line') or ''))
        if (spec.quality is DevourQuality.CREATURE and 'creature' not in types
                or spec.quality is DevourQuality.ARTIFACT and 'artifact' not in types
                or spec.quality is DevourQuality.FOOD and not ('artifact' in types and 'food' in subtypes)):
            raise error_type('Devour selected quality changed before replacement resume')


def devoured_entry_creature_count(prepared) -> int:
    """Read selected sacrifices whose linked plans left the battlefield."""
    sacrifices = {plan.object_id: plan for plan in prepared.devour_sacrifices}
    selected = set()
    count = 0
    for raw in prepared.event.payload.get("devour_proposals", ()):
        proposal = DevourProposal.from_dict(raw)
        for subject in proposal.objects:
            linked = sacrifices.get(subject.object_id)
            if (subject.object_id in selected or linked is None
                    or linked.logical_object_id != subject.logical_object_id
                    or linked.origin != "battlefield" or linked.destination == "battlefield"
                    or linked.transition_kind is not ZoneTransitionKind.SACRIFICE):
                raise ValueError("A Devour result lost a distinct committed sacrifice")
            selected.add(subject.object_id)
            if proposal.spec.quality is DevourQuality.CREATURE:
                count += 1
    if selected != sacrifices.keys():
        raise ValueError("A Devour receipt cannot count an unrelated sacrifice")
    return count


def devour_entry_reader_present(host, card) -> bool:
    from .entry_copy import copiable_oracle_id

    return any(
        DEVOUR_ENTRY_RESULT_CAPABILITY in program.capability_dependencies
        and host.semantic_program_is_current_trusted(program)
        for program in host.semantics.programs_for_oracle(
            copiable_oracle_id(card), active_zone="battlefield", event="permanent.enter.self",
        )
    )


def commit_devour_entry_receipt(host, prepared, card):
    """Only new, trusted consumers opt an entry into the retained receipt."""
    if card.zone != "battlefield" or prepared.origin == "battlefield":
        return
    if not devour_entry_reader_present(host, card):
        return
    if prepared.object_id != card.object_id:
        raise ValueError("A Devour entry receipt changed its prepared physical entrant")
    receipt = DevourEntryReceipt(card.object_id, card.logical_object_id, devoured_entry_creature_count(prepared))
    card.annotations[DEVOUR_ENTRY_RECEIPT_FIELD] = receipt.to_dict()


def _contains_devour_amount(value):
    if isinstance(value, Mapping):
        return value.get("kind") == DEVOUR_ENTRY_RESULT_KIND or any(
            _contains_devour_amount(child) for child in value.values()
        )
    return isinstance(value, (list, tuple)) and any(_contains_devour_amount(child) for child in value)


def devour_entry_result_node_capabilities(*, effects, target_schema, mechanic_ids):
    if not _contains_devour_amount(effects):
        return ()
    if len(effects) != 1:
        raise ValueError("A Devour entry result requires exactly one ordinary instruction")
    from .replacement.immutable import FrozenMap, thaw_value
    from .rules.node_capability_shapes import fixed_draw_node_capabilities
    from .rules.fixed_controller_effect_shapes import fixed_life_node_capabilities
    from .rules.affected_player_discard_capability_shapes import fixed_affected_player_discard_node_capabilities

    effect = thaw_value(FrozenMap(effects[0]))
    schema = thaw_value(target_schema)
    leaf = {
        "draw": ("count", fixed_draw_node_capabilities, ("cr-121-drawing-a-card",)),
        "life": ("delta", fixed_life_node_capabilities, ("cr-119-life",)),
        "choose_cards_apnap": ("count", fixed_affected_player_discard_node_capabilities,
                              ("fixed-affected-player-discard", "cr-402-hand", "cr-115-targets")),
    }.get(effect.get("op"))
    if leaf is None:
        raise ValueError("Unsupported Devour result operation")
    field, owner, mechanics = leaf
    amount = DevourEntryAmount.from_dict(effect[field])
    if effect["op"] != "life" and amount.coefficient != 1:
        raise ValueError("Devour draw and discard require one card per sacrificed creature")
    effect[field] = amount.coefficient
    if _contains_devour_amount(effect):
        raise ValueError("Only the Devour result amount can depend on its receipt")
    required = owner(effects=(effect,), target_schema=schema, mechanic_ids=mechanics)
    if not required:
        raise ValueError("A Devour result requires its exact independent fixed owner")
    if effect["op"] in {"draw", "life"} and (effect.get("player") != "$controller" or schema is not None):
        raise ValueError("A Devour draw or life result belongs to the original trigger controller")
    if effect["op"] == "choose_cards_apnap" and (
            effect.get("target") != "$target.0" or schema.get("player_relation") != "any"):
        raise ValueError("A Devour discard result requires its original target player")
    return (*required, DEVOUR_CAPABILITY, DEVOUR_ENTRY_RESULT_CAPABILITY)


def devour_entry_result_runtime_dependencies(program):
    if not _contains_devour_amount((program.effects, program.target_schema, program.cost_schema, program.handlers)):
        return ()
    if ((program.event, program.active_zone) != ("permanent.enter.self", "battlefield")
            or program.event_condition is not None or program.cost_schema is not None or program.handlers
            or _contains_devour_amount(program.target_schema)):
        raise ValueError("A Devour result requires its ordinary self-entry trigger")
    return (*devour_entry_result_node_capabilities(
        effects=program.effects, target_schema=program.target_schema, mechanic_ids=program.coverage,
    ), "trigger.event.normalized_zone_change", "trigger.placement.apnap")


def is_closed_devour_entry_result_program(program):
    try:
        required = devour_entry_result_runtime_dependencies(program)
    except (KeyError, TypeError, ValueError):
        return False
    return bool(required) and set(required).issubset(program.capability_dependencies)
