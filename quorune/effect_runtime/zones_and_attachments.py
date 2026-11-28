from __future__ import annotations

import random
from typing import Any, Mapping, Sequence

from ..attachments import attach_objects, detach_object
from ..attachment_source_return import apply_attachment_source_return
from ..rules.reconfigure import source_has_reconfigure, commit_reconfigure_attachment_effect
from ..rules.attachment_actions import (
    ENTRY_ATTACHMENT_VERSION, PLANESWALKER_EQUIP_RESULT, PLANESWALKER_EQUIP_SEMANTIC_KEY,
    TARGETED_EQUIPMENT_VERSION, equipment_attachment_is_legal,
    resolve_targeted_equipment_attachment, validate_entry_equipment_attachment,
)
from ..aura import legal_aura_target_refs
from ..continuous_effects import ContinuousOperation, Layer
from ..continuous_effect_state import (
    ContinuousEffectStateError,
    ResolutionEffectSource,
    create_resolution_continuous_effect,
    matching_battlefield_objects,
    resolution_effect_source,
)
from ..destruction import destroy_permanent_refs
from ..errors import GameRuleError
from ..effect_contracts import (
    effect_family_contract,
    REANIMATE_OPERATION,
)
from ..keyword_abilities import FIXED_CHARACTERISTIC_KEYWORDS
from ..model import CardInstance
from ..milling import mill_cards, MillRequest
from ..linked_exile_return import resolve_linked_exile_return, return_linked_exiled_objects
from .library_top_exile import apply_exile_top_library_card
from ..object_predicate import ObjectQueryError, ObjectQuerySpec
from ..targets import TargetGroup
from ..public_zone_moves import (
    GRAVEYARD_LIBRARY_TARGET_FIELD, STACK_SPELL_EXILE_FIELD, StackSpellExileScope, StackSpellExileSpec,
)
from ..rules.reanimation_followup import REANIMATION_FOLLOWUP_VERSIONS, wrap_gift_reanimation
from ..util import unique_preserving_order
from ..zone_trigger_events import ZoneTransitionKind
from .linked_zone_movement import apply_move_if_in_zone, commit_resolved_zone_movement
from .recover import apply_recover_source_result

_EXILE_ZONE = "exile"


OPERATIONS = effect_family_contract("zones-and-attachments.v1").operations


def _apply_stack_spell_exile(host, effect, *, actor, reason):
    from ..zone_transitions import ZoneTransitionOwner

    try:
        spec = StackSpellExileSpec.from_dict(effect[STACK_SPELL_EXILE_FIELD])
        source_ref = ResolutionEffectSource.from_effect(effect).stack_ref
    except (ValueError, TypeError, ContinuousEffectStateError) as exc:
        raise GameRuleError(str(exc)) from exc
    selected = spec.scope is StackSpellExileScope.SELECTED
    allowed = {"op", STACK_SPELL_EXILE_FIELD, "_runtime_source", "reason", "_replacement_selections"}
    if selected:
        allowed.add("stacks")
    if set(effect) - allowed or effect.get("op") != "exile":
        raise GameRuleError("Stack exile effect fields are closed")
    stack = {item.ref: item for item in host.state.stack}
    source = stack.get(source_ref)
    if (source is None or source.kind not in {"spell", "spell_copy"} or source.controller != actor
            or source.context.get("currently_resolving") is not True):
        raise GameRuleError("Stack exile requires its current resolving source")
    spell_kinds = {"spell", "spell_copy"}
    if selected:
        refs = effect.get("stacks")
        if (not isinstance(refs, (list, tuple)) or any(type(ref) is not str for ref in refs)
                or len(set(refs)) != len(refs) or source_ref in refs):
            raise GameRuleError("Stack exile requires distinct other spell targets")
        spells = [stack[ref] for ref in refs if ref in stack]
        if any(item.kind not in spell_kinds for item in spells):
            raise GameRuleError("Stack exile targets must be spells")
        abilities = ()
    else:
        spells = [item for item in stack.values() if item.ref != source_ref and item.kind in spell_kinds]
        abilities = tuple(item.ref for item in stack.values() if item.kind in {"activated_ability", "triggered_ability"})
    moves = []
    for item in spells:
        card = host.state.cards.get(item.card_object_id)
        if card is None or card.zone != "stack":
            raise GameRuleError("An exiled spell is missing its current stack object")
        moves.append((card.object_id, "exile"))
    with host.transaction():
        moved = ZoneTransitionOwner(host).move_cards_simultaneously(
            moves, reason=reason, log=False, replacement_selections=tuple(effect.get("_replacement_selections") or ()),
        )
        for ref in abilities:
            host._counter_stack_item(ref, reason=reason, countered_by=actor)
    return moved


def _apply_bounce_or_destroy_or_discard_or_exile_or_move_or_sacrifice(
    host: Any,
    effect: Mapping[str, Any],
    *,
    actor: str,
    operation: str,
    reason: str,
) -> Any:
    from ..rules.linked_hand_mana_model import PAIR

    op = operation
    if op == "exile" and PAIR in effect:
        from .linked_hand_mana import apply_linked_hand_imprint

        return apply_linked_hand_imprint(host, effect, actor=actor, reason=reason)
    if op == "move" and "attacking_entry" in effect:
        from ..rules.attacking_card_entry import apply_attacking_entry_move

        return apply_attacking_entry_move(host, effect, actor=actor, reason=reason)
    if op == "exile" and STACK_SPELL_EXILE_FIELD in effect:
        return _apply_stack_spell_exile(host, effect, actor=actor, reason=reason)
    if op == "exile" and "cast_spell_suspend_version" in effect:
        from .cast_spell_suspend import apply_cast_spell_suspend

        return apply_cast_spell_suspend(host, effect, actor=actor, reason=reason)
    from ..rules.opponent_spell_or_permanent_return import (
        ENTRY_FIELD,
        FIELD as OPPONENT_RETURN_FIELD,
        SPELL_FIELD,
        COPY_RETURN_FIELD,
    )

    if op == "move" and {OPPONENT_RETURN_FIELD, ENTRY_FIELD, SPELL_FIELD, COPY_RETURN_FIELD}.intersection(effect):
        from .opponent_spell_or_permanent_return import apply_opponent_spell_or_permanent_return

        return apply_opponent_spell_or_permanent_return(host, effect, actor=actor, reason=reason)
    graveyard_library_target = GRAVEYARD_LIBRARY_TARGET_FIELD in effect
    if graveyard_library_target:
        version = effect[GRAVEYARD_LIBRARY_TARGET_FIELD]
        if (type(version) is not int or version != 1 or op != "move"
                or effect.get("destination") != "library" or effect.get("position") not in ("top", "bottom")):
            raise GameRuleError("Invalid graveyard library target movement")
        if effect["card"] is None:
            return None
    implicit_zones = {
        "sacrifice": {"battlefield"},
        "destroy": {"battlefield"},
        "bounce": {"battlefield"},
        "discard": {"hand"},
    }
    if graveyard_library_target:
        implicit_zones["move"] = {"graveyard"}
    followup = None
    if 'hand_entry_query' in effect:
        from ..hand_entry_move import resolve_selected_hand_entry_card
        from ..rules.hand_entry_followup import decode_hand_entry_followup
        from ..continuous_effect_state import ResolutionEffectSource

        card=resolve_selected_hand_entry_card(host,effect,actor=actor)
        followup = decode_hand_entry_followup(effect)
        if followup is not None:
            try:
                source = ResolutionEffectSource.from_effect(effect)
            except ContinuousEffectStateError as exc:
                raise GameRuleError(str(exc)) from exc
            previous_zone_counter = card.zone_change_counter
    else:
        card = host._resolve_object(actor,str(effect["card"]),zones=implicit_zones.get(op))
    if op == "destroy":
        result = destroy_permanent_refs(
            host,
            (card.ref,),
            actor=actor,
            reason=reason,
            replacement_selections=tuple(
                effect.get("_replacement_selections") or ()
            ),
        )
        return card if result.destroyed_object_ids else None
    destination = {
        "sacrifice": "graveyard",
        "destroy": "graveyard",
        "exile": "exile",
        "bounce": "hand",
        "discard": "graveyard",
    }.get(op, str(effect.get("destination") or "graveyard"))
    from ..discard_effect_origin import authorized_legacy_discard_origin

    try:
        discard_origin = authorized_legacy_discard_origin(host.state, effect, controller=actor)
    except ValueError as exc:
        raise GameRuleError(str(exc)) from exc
    moved = commit_resolved_zone_movement(
        host, card, effect, destination=destination, reason=reason, discard_origin=discard_origin,
        putting_player=actor if destination == "battlefield" and card.zone != "battlefield" else None,
        transition_kind=(ZoneTransitionKind.DISCARD if op == "discard" else ZoneTransitionKind.SACRIFICE
                         if op == "sacrifice" else ZoneTransitionKind.ORDINARY),
    )
    if followup is not None:
        from ..rules.hand_entry_followup import HandEntrySubtypeLife, committed_hand_entry_life
        from ..rules.reanimation_followup import commit_battlefield_entry_followup

        if isinstance(followup, HandEntrySubtypeLife):
            return committed_hand_entry_life(host, moved, previous_zone_counter=previous_zone_counter,
                actor=actor, source=source, followup=followup)
        commit_battlefield_entry_followup(host, moved, previous_zone_counter=previous_zone_counter,
            actor=actor, source=source, followup=followup)
    return moved



def _apply_prepare_graveyard_creature_aura(
    host: Any,
    effect: Mapping[str, Any],
    *,
    actor: str,
    operation: str,
    reason: str,
) -> Any:
    op = operation
    aura = host._resolve_object(
        actor,
        str(effect["aura"]),
        zones={"stack"},
    )
    target = host._resolve_object(
        actor,
        str(effect["card"]),
        zones={"graveyard"},
    )
    types, _, _ = host._type_parts(
        str(
            host._effective_card_data(target).get(
                "type_line"
            )
            or ""
        )
    )
    if "creature" not in types:
        raise GameRuleError(
            "The Aura requires a creature card in a graveyard"
        )
    aura.annotations["pending_aura_target"] = target.ref
    return target.ref



def _apply_bestow_prepare(
    host: Any,
    effect: Mapping[str, Any],
    *,
    actor: str,
    operation: str,
    reason: str,
) -> Any:
    op = operation
    aura = host._resolve_object(
        actor,
        str(effect["aura"]),
        zones={"stack"},
    )
    target = host._resolve_object(
        actor,
        str(effect["card"]),
        zones={"battlefield"},
    )
    types, _, _ = host._type_parts(
        str(
            host._effective_card_data(target).get(
                "type_line"
            )
            or ""
        )
    )
    if "creature" not in types:
        raise GameRuleError(
            "Bestow requires a creature target"
        )
    aura.annotations["bestowed"] = True
    aura.annotations["pending_aura_target"] = target.ref
    aura.annotations["pending_aura_zone"] = "battlefield"
    return target.ref



def _apply_reanimate_attached_creature_aura(
    host: Any,
    effect: Mapping[str, Any],
    *,
    actor: str,
    operation: str,
    reason: str,
) -> Any:
    if "aura_incarnation" in effect:
        from ..aura.runtime import reanimate_graveyard_attachment

        try:
            source = ResolutionEffectSource.from_effect(effect)
        except ContinuousEffectStateError as exc:
            raise GameRuleError(str(exc)) from exc
        aura = host.state.cards.get(source.object_id)
        if (
            aura is None or aura.zone != "battlefield" or aura.phased_out
            or aura.ref != effect.get("aura")
            or aura.logical_object_id != effect["aura_incarnation"]
        ):
            return None
        return reanimate_graveyard_attachment(host, aura, actor=actor, reason=reason, effect=effect)

    # A saved Game Record v3 can supply the earlier reviewed effect body.
    aura = host._resolve_object(
        actor,
        str(effect["aura"]),
        zones={"battlefield"},
        controlled_only=True,
    )
    target_id = aura.attached_to
    if target_id not in host.state.cards:
        return None
    creature = host.state.cards[target_id]
    if creature.zone != "graveyard":
        return None
    types, _, _ = host._type_parts(
        str(
            host._effective_card_data(creature).get(
                "type_line"
            )
            or ""
        )
    )
    if "creature" not in types:
        return None
    host.move_card(
        creature.object_id,
        "battlefield",
        controller=actor,
        putting_player=actor,
        reason=reason,
        semantic_events=True,
    )
    attach_objects(
        host.state.cards,
        aura,
        creature,
        source_timestamp=host._next_zone_timestamp(),
        players=host.state.players,
    )
    link_annotation = str(
        effect.get("link_annotation") or "reanimated_creature"
    )
    aura.annotations[link_annotation] = creature.object_id
    host._log(
        actor,
        str(effect.get("event_code") or "aura.reanimate"),
        (
            f"{actor} returned {creature.ref} and attached "
            f"{aura.ref} to it."
        ),
        {
            "aura": aura.ref,
            "creature": creature.ref,
        },
        importance=2,
        changed_objects=[aura.object_id, creature.object_id],
        changed_players=[actor],
    )
    return creature.ref

def _attachment_object(host: Any, actor: str, value: str) -> CardInstance:
    identity_matches = [
        card
        for card in host.state.cards.values()
        if card.object_id == value or card.ref.casefold() == value.casefold()
    ]
    if len(identity_matches) == 1:
        return identity_matches[0]
    return host._resolve_object(actor, value, zones={"battlefield"})


def _resolved_attachment_kind(
    host: Any,
    attachment: CardInstance,
    requested: Any,
) -> str:
    types, subtypes, _ = host._type_parts(
        str(host._effective_card_data(attachment).get("type_line") or "")
    )
    kind = str(
        requested
        or (
            "equipment"
            if "artifact" in types and "equipment" in subtypes
            else "aura"
            if "enchantment" in types and "aura" in subtypes
            else ""
        )
    ).casefold()
    if kind not in {"aura", "equipment"}:
        raise GameRuleError("Attach has an unsupported source kind")
    return kind


def _attachment_target_is_legal(
    host: Any,
    attachment: CardInstance,
    creature: CardInstance,
    *,
    attachment_kind: str,
) -> bool:
    if attachment_kind == "equipment":
        return equipment_attachment_is_legal(host, attachment, creature)
    attachment_types, attachment_subtypes, _ = host._type_parts(
        str(host._effective_card_data(attachment).get("type_line") or "")
    )
    creature_types, _, _ = host._type_parts(
        str(host._effective_card_data(creature).get("type_line") or "")
    )
    if "creature" not in creature_types:
        return False
    enchant_spec = host._compiled_enchant_spec(attachment)
    return bool(
        "enchantment" in attachment_types
        and "aura" in attachment_subtypes
        and enchant_spec is not None
        and creature.ref
        in legal_aura_target_refs(
            host,
            attachment,
            enchant_spec,
            controller=attachment.controller,
            as_target=False,
        )
    )


def _planeswalker_equip_is_current(host, effect, *, equipment, target, actor):
    source = ResolutionEffectSource.from_effect(effect)
    stack = host.state.stack
    item = stack[-1] if stack else None
    if (type(effect[PLANESWALKER_EQUIP_RESULT]) is not int or effect[PLANESWALKER_EQUIP_RESULT] != 1
            or set(effect) != {"op", "equipment", "creature", "reason", PLANESWALKER_EQUIP_RESULT, "_runtime_source"}
            or item is None or item.ref != source.stack_ref or item.kind != "activated_ability"
            or item.semantic_key != PLANESWALKER_EQUIP_SEMANTIC_KEY or item.controller != actor
            or item.context.get("currently_resolving") is not True):
        raise GameRuleError("Equip planeswalker requires its resolving ability")
    return (
        source.object_id == item.source_object_id == equipment.object_id
        and source.logical_object_id == equipment.logical_object_id
        and item.targets == [target.ref]
        and target.controller == actor
        and not equipment.phased_out and not target.phased_out
    )


def _resolution_attachment_is_legal(host, effect, attachment, target, *, kind, actor):
    if PLANESWALKER_EQUIP_RESULT not in effect:
        return _attachment_target_is_legal(host, attachment, target, attachment_kind=kind)
    return (
        _planeswalker_equip_is_current(host, effect, equipment=attachment, target=target, actor=actor)
        and kind == "equipment"
        and equipment_attachment_is_legal(host, attachment, target, recipient_type="planeswalker")
    )


def _apply_attach_instruction(host, effect, *, actor, operation, reason):
    from ..manifest import MANIFEST_ATTACHMENT_RESULT

    if MANIFEST_ATTACHMENT_RESULT in effect:
        from ..rules.manifest_shapes import manifested_attachment_is_current

        try:
            if not manifested_attachment_is_current(host.state.cards, effect):
                return None
        except ValueError as exc:
            raise GameRuleError(str(exc)) from exc
    if TARGETED_EQUIPMENT_VERSION in effect:
        try:
            attachment, recipient = resolve_targeted_equipment_attachment(host, effect, actor=actor)
        except ValueError as exc:
            raise GameRuleError(str(exc)) from exc
        if attachment is None or recipient is None:
            return None
        effect = {**effect, "source": attachment, "target": recipient}
    return _apply_attach(host, effect, actor=actor, operation=operation, reason=reason)


def _apply_attach(
    host: Any,
    effect: Mapping[str, Any],
    *,
    actor: str,
    operation: str,
    reason: str,
) -> Any:
    op = operation
    if ENTRY_ATTACHMENT_VERSION in effect:
        try:
            validate_entry_equipment_attachment(effect, resolved=True)
        except ValueError as exc:
            raise GameRuleError(str(exc)) from exc
    legacy_equipment_shape = (
        "equipment" in effect
        and "creature" in effect
        and "source" not in effect
        and "target" not in effect
    )
    has_source = "equipment" in effect or "source" in effect
    has_target = "creature" in effect or "target" in effect
    source_value = effect.get("equipment", effect.get("source"))
    target_value = effect.get("creature", effect.get("target"))
    if not has_source or not has_target:
        raise GameRuleError("Attach requires a source and target")
    if source_value is None or target_value is None:
        if isinstance(effect.get("_runtime_source"), Mapping):
            return None
        raise GameRuleError("Attach requires a source and target")
    attachment_value = str(source_value)
    creature_value = str(target_value)

    attachment = _attachment_object(host, actor, attachment_value)
    if attachment.zone != "battlefield":
        details = {
            ("equipment" if legacy_equipment_shape else "attachment"): attachment.ref,
            "creature": creature_value,
            "reason": reason,
            "result": (
                "equipment_not_on_battlefield"
                if legacy_equipment_shape
                else "source_not_on_battlefield"
            ),
        }
        host._log(
            actor,
            "attachment.no_effect",
            (
                f"{attachment.ref} could not become attached because "
                "it was no longer on the battlefield."
            ),
            details,
            importance=2,
        )
        return None
    creature = _attachment_object(host, actor, creature_value)
    if TARGETED_EQUIPMENT_VERSION in effect and (attachment.phased_out or creature.phased_out):
        return None
    if creature.zone != "battlefield":
        details = {
            ("equipment" if legacy_equipment_shape else "attachment"): attachment.ref,
            "creature": creature.ref,
            "reason": reason,
            "result": "creature_not_on_battlefield",
        }
        host._log(
            actor,
            "attachment.no_effect",
            (
                f"{attachment.ref} could not become attached because "
                f"{creature.ref} was no longer on the battlefield."
            ),
            details,
            importance=2,
        )
        return None
    attachment_kind = _resolved_attachment_kind(
        host,
        attachment,
        effect.get("attachment_kind"),
    )
    if not _resolution_attachment_is_legal(host, effect, attachment, creature, kind=attachment_kind, actor=actor):
        if legacy_equipment_shape and not isinstance(effect.get('_runtime_source'), Mapping):
            raise GameRuleError(
                "Attach requires an Equipment and a creature"
            )
        host._log(
            actor,
            "attachment.no_effect",
            f"{attachment.ref} could not legally attach to {creature.ref}.",
            {
                "attachment": attachment.ref,
                "creature": creature.ref,
                "reason": reason,
                "result": "illegal_attachment",
            },
            importance=2,
        )
        return None
    from ..unattachment_triggers import dispatch_unattachment, prepare_equipment_unattachment

    has_reconfigure = attachment_kind == 'equipment' and source_has_reconfigure(host, attachment)
    former = (prepare_equipment_unattachment(host, attachment)
              if attachment.attached_to != creature.object_id else None)
    transition = attach_objects(
        host.state.cards,
        attachment,
        creature,
        source_timestamp=(
            attachment.zone_timestamp
            if attachment.attached_to == creature.object_id
            else host._next_zone_timestamp()
        ),
        players=host.state.players,
    )
    if not transition.changed:
        return creature.ref
    if has_reconfigure:
        commit_reconfigure_attachment_effect(host, attachment, creature)
    host._log(
        actor,
        "attachment.attach",
        f"{attachment.ref} became attached to {creature.ref}.",
        (
            {
                "equipment": attachment.ref,
                "creature": creature.ref,
                "reason": reason,
            }
            if legacy_equipment_shape
            else {
                "attachment": attachment.ref,
                "attachment_kind": attachment_kind,
                "creature": creature.ref,
                "reason": reason,
            }
        ),
        importance=2,
        changed_objects=[
            attachment.object_id,
            creature.object_id,
        ],
    )
    dispatch_unattachment(host, former)
    return creature.ref


def _apply_unattach(host: Any, effect: Mapping[str, Any], *, actor: str,
        operation: str, reason: str) -> Any:
    value = effect.get('source')
    if value is None and isinstance(effect.get('_runtime_source'), Mapping):
        return None
    if value is None:
        raise GameRuleError('Unattach requires a source')
    source = _attachment_object(host, actor, str(value))
    if source.zone != 'battlefield':
        return None
    from ..unattachment_triggers import dispatch_unattachment, prepare_equipment_unattachment
    former = prepare_equipment_unattachment(host, source)
    transition = detach_object(host.state.cards, source, players=host.state.players)
    if transition.changed:
        host._log(actor, 'attachment.unattach', f'{source.ref} became unattached.',
            {'attachment':source.ref, 'reason':reason}, importance=2)
        dispatch_unattachment(host, former)
    return source


def _apply_create_attached_token(
    host: Any,
    effect: Mapping[str, Any],
    *,
    actor: str,
    operation: str,
    reason: str,
) -> Any:
    op = operation
    allowed = {
        "op",
        "source",
        "controller",
        "name",
        "quantity",
        "characteristics",
        "_replacement_selections",
        "_runtime_source",
    }
    source = effect.get("source")
    runtime_source = effect.get("_runtime_source")
    controller = effect.get("controller")
    name = effect.get("name")
    characteristics = effect.get("characteristics")
    if (
        set(effect) - allowed
        or effect.get("quantity") != 1
        or "source" not in effect
        or not (
            isinstance(source, str)
            and bool(source)
            or source is None
            and isinstance(runtime_source, Mapping)
        )
        or (controller is not None and not isinstance(controller, str))
        or not isinstance(name, str)
        or not name
        or not isinstance(characteristics, Mapping)
    ):
        raise GameRuleError("Attached token creation has a closed fixed shape")
    created = host.create_token(
        str(effect.get("controller") or actor),
        name=name,
        quantity=1,
        characteristics=dict(characteristics),
        reason=reason,
        replacement_selections=tuple(
            effect.get("_replacement_selections") or ()
        ),
    )
    if created and source is not None:
        _apply_attach(
            host,
            {
                "attachment_kind": "equipment",
                "source": source,
                "target": created[0],
            },
            actor=actor,
            operation="attach",
            reason=reason,
        )
    return created



def _apply_exchange_artifact_zones(
    host: Any,
    effect: Mapping[str, Any],
    *,
    actor: str,
    operation: str,
    reason: str,
) -> Any:
    del operation
    typed = "schema_version" in effect
    if typed and (
        type(effect["schema_version"]) is not int or effect["schema_version"] != 1
        or set(effect) - {"op", "schema_version", "battlefield_card", "graveyard_card",
                          "_replacement_selections", "reason", "_runtime_source"}
        or not {"battlefield_card", "graveyard_card"} <= set(effect)
        or any(effect[key] is not None and (type(effect[key]) is not str or not effect[key])
               for key in ("battlefield_card", "graveyard_card"))
    ):
        raise GameRuleError("Artifact sacrifice and return requires both authorized target slots")
    battlefield_ref = effect.get("battlefield_card")
    graveyard_ref = effect.get("graveyard_card")
    if not battlefield_ref or not graveyard_ref:
        return None
    try:
        battlefield_card = host._resolve_object(
            actor,
            str(battlefield_ref),
            zones={"battlefield"},
        )
        graveyard_card = host._resolve_object(
            actor,
            str(graveyard_ref),
            zones={"graveyard"},
        )
    except GameRuleError:
        return None
    battlefield_types, _, _ = host._type_parts(
        str(
            host._effective_card_data(battlefield_card).get(
                "type_line"
            )
            or ""
        )
    )
    graveyard_types, _, _ = host._type_parts(
        str(
            host._effective_card_data(graveyard_card).get(
                "type_line"
            )
            or ""
        )
    )
    if (
        "artifact" not in battlefield_types
        or "artifact" not in graveyard_types
        or battlefield_card.controller != graveyard_card.owner
        or typed and (battlefield_card.phased_out or not graveyard_card.is_card_object
                      or battlefield_card.controller not in host.active_seats)
    ):
        return None
    movement = {}
    if typed:
        controller = battlefield_card.controller
        selections = effect.get("_replacement_selections", ())
        if not isinstance(selections, (list, tuple)):
            raise GameRuleError("Artifact sacrifice and return replacement choices are malformed")
        movement = {
            "transition_kinds": {battlefield_card.object_id: ZoneTransitionKind.SACRIFICE},
            "destination_controllers": {graveyard_card.object_id: controller},
            "putting_players": {graveyard_card.object_id: controller},
            "replacement_selections": tuple(selections),
        }
    host._move_cards_simultaneously(
        [
            (battlefield_card.object_id, "graveyard"),
            (graveyard_card.object_id, "battlefield"),
        ],
        reason=reason,
        log=True,
        **movement,
    )
    return [battlefield_card.ref, graveyard_card.ref]



def _apply_mill(
    host: Any,
    effect: Mapping[str, Any],
    *,
    actor: str,
    operation: str,
    reason: str,
) -> Any:
    del operation
    from ..rules.source_counter_mill_choice import COMMIT_FIELD, commit_source_counter_mill

    if COMMIT_FIELD in effect:
        return commit_source_counter_mill(host, effect, actor=actor, reason=reason)
    if "scope" in effect:
        from ..milling import mill_player_batch, mill_request
        from ..query_effect_amount_model import OPPONENT_HAND_MILL_KIND, OpponentHandMillCount
        scope = effect.get("scope")
        if (set(effect) - {"op", "scope", "count", "reason", "_runtime_source"}
                or scope not in {"all_active_players", "controller_opponents"}):
            raise GameRuleError("All-player Mill instruction is malformed")
        count = effect.get("count")
        if isinstance(count, Mapping) and count.get("kind") == OPPONENT_HAND_MILL_KIND:
            try:
                count = OpponentHandMillCount.from_dict(count, scope=scope)
            except ValueError as exc:
                raise GameRuleError(str(exc)) from exc
        elif type(count) is not int or count < 0:
            raise GameRuleError("All-player Mill instruction is malformed")
        results = mill_player_batch(host, tuple(
            mill_request(host, actor=actor, player=seat, count=count, reason=reason)
            for seat in host.apnap_order()
            if effect['scope']!='controller_opponents' or seat!=actor
        ))
        return [ref for result in results for ref in result.refs]
    count = max(0, int(effect.get("count", 1)))
    if not count:
        return []
    result = mill_cards(
        host,
        MillRequest(
            actor=actor,
            player=str(effect.get("player") or actor),
            count=count,
            reason=reason,
        ),
    )
    return list(result.refs)



def _apply_reanimate(
    host: Any,
    effect: Mapping[str, Any],
    *,
    actor: str,
    operation: str,
    reason: str,
) -> Any:
    from ..event_card_return import current_death_source_return_effect
    from ..rules.reanimation_followup import reanimation_entry_handler

    if (entry_handler := reanimation_entry_handler(effect)) is not None:
        return entry_handler(host, effect, actor=actor, reason=reason)
    if 'attachment_return' in effect or effect.get('schema_version') == 7:
        return apply_attachment_source_return(host, effect, actor=actor, operation=operation, reason=reason)
    effect = current_death_source_return_effect(host, effect)
    if effect is None:
        return None
    followup = None
    if effect.get("schema_version") in REANIMATION_FOLLOWUP_VERSIONS:
        from ..continuous_effect_state import ResolutionEffectSource
        from ..rules.reanimation_followup import current_linked_subject, validate_reanimation_followup

        try:
            followup = validate_reanimation_followup(effect, materialized=True)
            source = ResolutionEffectSource.from_effect(effect)
        except (ValueError, TypeError) as exc:
            raise GameRuleError(str(exc)) from exc
        subject = effect["target_snapshot"]
        if current_linked_subject(host, subject, zone="graveyard") is None:
            return None
        effect = {key: value for key, value in effect.items() if key not in {"schema_version", "followup", "target_snapshot"}}
    allowed = {
        "op",
        "card",
        "controller",
        "tapped",
        "reason",
        "_replacement_selections",
        "_runtime_source",
        "_aura_target_ref",
    }
    if set(effect) - allowed or not {
        "op",
        "card",
        "controller",
    }.issubset(effect):
        raise GameRuleError("Reanimation effect has an invalid shape")
    if effect.get("op") != REANIMATE_OPERATION:
        raise GameRuleError("Reanimation operation is unsupported")
    controller = effect.get("controller")
    if type(controller) is not str or not controller:
        raise GameRuleError("Reanimation controller is malformed")
    host._require_seat(controller, in_game=True)
    card_ref = effect.get("card")
    if type(card_ref) is not str or not card_ref:
        raise GameRuleError("Reanimation card reference is malformed")
    tapped = effect.get("tapped", False)
    if type(tapped) is not bool:
        raise GameRuleError("Reanimation tapped state is malformed")
    selections = effect.get("_replacement_selections", ())
    if not isinstance(selections, (list, tuple)):
        raise GameRuleError("Reanimation replacement selections are malformed")
    aura_target_ref = effect.get("_aura_target_ref")
    if aura_target_ref is not None and (
        type(aura_target_ref) is not str or not aura_target_ref
    ):
        raise GameRuleError("Reanimation Aura target is malformed")
    card = host._resolve_object(
        actor,
        card_ref,
        zones={"graveyard"},
    )
    types, _, _ = host._type_parts(
        str(
            host._effective_card_data(card).get("type_line")
            or ""
        )
    )
    if not types.intersection(
        {
            "artifact",
            "battle",
            "creature",
            "enchantment",
            "land",
            "planeswalker",
        }
    ):
        raise GameRuleError(
            "Reanimation effect requires a permanent card"
        )
    entered = host.move_card(
        card.object_id,
        "battlefield",
        controller=controller,
        putting_player=actor,
        tapped=tapped,
        reason=reason,
        semantic_events=True,
        replacement_selections=tuple(selections),
        aura_target_ref=aura_target_ref,
    )
    if followup is not None:
        from ..rules.reanimation_followup import commit_battlefield_entry_followup

        commit_battlefield_entry_followup(host, entered, previous_zone_counter=subject["zone_change_counter"],
            actor=actor, source=source, followup=followup)
    return entered



def _apply_exile_all(
    host: Any,
    effect: Mapping[str, Any],
    *,
    actor: str,
    operation: str,
    reason: str,
) -> Any:
    from ..rules.airbend import apply_airbend_creature_set, uses_airbend_creature_set

    if uses_airbend_creature_set(effect):
        return apply_airbend_creature_set(host, effect, actor=actor, reason=reason)
    specification = dict(effect.get("filter") or {})
    specification.setdefault("zones", ["battlefield"])
    specification.setdefault("categories", ["permanent"])
    specification.setdefault("min", 0)
    specification.setdefault("max", 10000)
    group = TargetGroup.from_mapping(
        specification,
        default_id="affected",
    )
    source_ref = str(effect.get("source") or "") or None
    refs = [
        str(row["ref"])
        for row in host._target_candidate_rows(actor, group)
        if host._target_row_matches(
            actor,
            group,
            row,
            source_ref=source_ref,
            as_target=False,
        )
    ]
    cards: list[CardInstance] = []
    for ref in refs:
        try:
            card = host._resolve_object(
                actor, ref, zones={"battlefield"}
            )
        except GameRuleError:
            continue
        cards.append(card)
    changes = [(card.object_id, _EXILE_ZONE) for card in cards]
    host._move_cards_simultaneously(changes, reason=reason, log=True)
    return [host.state.cards[object_id].ref for object_id, _ in changes]



def _apply_exile_opponent_graveyards(
    host: Any,
    effect: Mapping[str, Any],
    *,
    actor: str,
    operation: str,
    reason: str,
) -> Any:
    op = operation
    changes: list[tuple[str, str]] = []
    for opponent in host.active_seats:
        if opponent == actor:
            continue
        for object_id in list(
            host.state.players[opponent].zones["graveyard"]
        ):
            changes.append((object_id, "exile"))
    host._move_cards_simultaneously(
        changes,
        reason=reason,
        log=True,
    )
    return [host.state.cards[object_id].ref for object_id, _ in changes]



def _apply_exile_graveyard(
    host: Any,
    effect: Mapping[str, Any],
    *,
    actor: str,
    operation: str,
    reason: str,
) -> Any:
    op = operation
    player = str(effect["player"])
    if player not in host.active_seats:
        raise GameRuleError(
            "Graveyard exile requires an active player"
        )
    changes = [
        (object_id, "exile")
        for object_id in list(
            host.state.players[player].zones["graveyard"]
        )
    ]
    host._move_cards_simultaneously(
        changes,
        reason=reason,
        log=True,
    )
    return [
        host.state.cards[object_id].ref
        for object_id, _ in changes
    ]



def _apply_destroy_selected(
    host: Any,
    effect: Mapping[str, Any],
    *,
    actor: str,
    operation: str,
    reason: str,
) -> Any:
    op = operation
    refs: list[str] = []
    for raw_ref in effect.get("cards") or []:
        if raw_ref is None:
            continue
        try:
            card = host._resolve_object(
                actor,
                str(raw_ref),
                zones={"battlefield"},
            )
        except GameRuleError:
            continue
        refs.append(card.ref)
    result = destroy_permanent_refs(
        host,
        refs,
        actor=actor,
        reason=reason,
    )
    return [
        host.state.cards[object_id].ref
        for object_id in result.destroyed_object_ids
    ]



def _fixed_characteristic_keywords(
    effect: Mapping[str, Any],
) -> tuple[str, ...]:
    if "keywords" not in effect:
        return ()
    raw_keywords = effect["keywords"]
    if not isinstance(raw_keywords, list):
        raise GameRuleError("Fixed characteristic keywords must be a list")
    keywords = tuple(raw_keywords)
    if (
        not keywords
        or any(type(keyword) is not str for keyword in keywords)
        or len(set(keywords)) != len(keywords)
        or any(
            keyword not in FIXED_CHARACTERISTIC_KEYWORDS
            for keyword in keywords
        )
    ):
        raise GameRuleError(
            "Fixed characteristic keywords must be unique supported keywords"
        )
    return keywords


def _apply_modify_all_matching_permanents_until_end_of_turn(
    host: Any,
    effect: Mapping[str, Any],
    *,
    actor: str,
    operation: str,
    reason: str,
) -> Any:
    from ..rules.combat_keyword_share import VERSION as COMBAT_KEYWORD_SHARE_VERSION
    from ..rules.same_name_creature_modifiers import SAME_NAME_STAT_VERSION_FIELD, resolve_same_name_creature_stats
    from ..rules.target_player_stat_untap import TARGET_PLAYER_STAT_UNTAP_VERSION
    from .combat_keyword_share import apply_combat_keyword_share

    if COMBAT_KEYWORD_SHARE_VERSION in effect:
        return apply_combat_keyword_share(host, effect, actor=actor)
    from ..rules.counter_snapshot_protection import FIELD as COUNTER_SNAPSHOT_FIELD

    if COUNTER_SNAPSHOT_FIELD in effect:
        from .counter_snapshot_protection import apply_counter_snapshot_protection

        return apply_counter_snapshot_protection(host, effect, actor=actor, reason=reason)
    if TARGET_PLAYER_STAT_UNTAP_VERSION in effect:
        return _apply_target_player_stats_then_untap(host, effect, actor=actor, reason=reason)
    if SAME_NAME_STAT_VERSION_FIELD in effect:
        try:
            spec, affected = resolve_same_name_creature_stats(host, effect)
        except (ContinuousEffectStateError, ValueError) as exc:
            raise GameRuleError(str(exc)) from exc
        return _commit_temporary_stat_set(
            host, effect, affected, actor=actor, power_delta=spec.power, toughness_delta=spec.toughness,
        )
    if 'event_player_set_version' in effect:
        from ..rules.event_player_modifiers import prepare_event_player_modifier_runtime
        try:effect=prepare_event_player_modifier_runtime(host,effect)
        except (TypeError,ValueError,KeyError) as exc:raise GameRuleError(str(exc)) from exc
        if effect is None:return []
    from ..next_turn_effect import split_next_turn_effect

    try:
        effect, next_turn = split_next_turn_effect(effect)
    except ValueError as exc:
        raise GameRuleError(str(exc)) from exc
    if next_turn and host.state.continuous_effects is None:
        raise GameRuleError("Next-turn expiry requires the resolution effect journal")
    expiry_player = actor if next_turn else None
    keywords = _fixed_characteristic_keywords(effect)
    from ..rules.fixed_resolution_characteristic_shapes import (
        EACH_COLOR_PROTECTION_FIELD, fixed_set_each_color_protection_is_closed,
    )

    raw_predicate = effect.get("predicate")
    if raw_predicate is not None:
        try:
            predicate = ObjectQuerySpec.from_dict(raw_predicate)
        except ObjectQueryError as exc:
            raise GameRuleError(str(exc)) from exc
        power_delta = int(effect.get("power", 0))
        toughness_delta = int(effect.get("toughness", 0))
    else:
        amount = int(effect.get("amount", 0))
        scale = int(effect.get("scale", 1))
        required_type = str(
            effect.get("required_type") or ""
        ).casefold()
        if amount < 0 or not required_type:
            raise GameRuleError("Mass modification parameters are invalid")
        power_delta = int(effect.get("power_delta", 0)) + amount * scale
        toughness_delta = (
            int(effect.get("toughness_delta", 0)) + amount * scale
        )
        predicate = ObjectQuerySpec(
            zones=("battlefield",),
            types_all=(required_type,),
        )
    fragments = ()
    if EACH_COLOR_PROTECTION_FIELD in effect:
        if (next_turn or host.state.continuous_effects is None or not keywords
                or not fixed_set_each_color_protection_is_closed(effect, predicate, controller=actor)):
            raise GameRuleError("Protection from each color requires a closed resolution creature set")
        from ..ability_fragments import ProtectionQualityKind, ProtectionSpec, ability_fragment_to_dict

        fragments = tuple(
            ability_fragment_to_dict(ProtectionSpec(ProtectionQualityKind.COLOR, color))
            for color in "WUBRG"
        )
    try:
        affected = matching_battlefield_objects(host, predicate)
    except ContinuousEffectStateError as exc:
        raise GameRuleError(str(exc)) from exc
    return _commit_temporary_stat_set(
        host, effect, affected, actor=actor, power_delta=power_delta, toughness_delta=toughness_delta,
        keywords=keywords, expiry_player=expiry_player, fragments=fragments,
    )


def _apply_target_player_stats_then_untap(host, effect, *, actor, reason):
    from ..public_tap_state_sets import commit_public_tap_state_snapshot, snapshot_public_tap_state
    from ..rules.target_player_stat_untap import decode_target_player_stat_untap

    try:
        spec, player = decode_target_player_stat_untap(effect, resolved=True)
        if player not in host.affected_permanent_active_seats():
            return []
        affected = matching_battlefield_objects(host, spec.receivers(player))
        snapshot = snapshot_public_tap_state(affected)
        changed = _commit_temporary_stat_set(
            host, effect, affected, actor=actor, power_delta=spec.power, toughness_delta=spec.toughness,
        )
        commit_public_tap_state_snapshot(host, snapshot, actor=actor, tapped=False, reason=reason)
    except (TypeError, ValueError) as exc:
        raise GameRuleError(str(exc)) from exc
    return changed


def _commit_temporary_stat_set(host, effect, affected, *, actor, power_delta, toughness_delta,
                              keywords=(), expiry_player=None, fragments=()):
    affected_ids = [card.object_id for card in affected]
    if host.state.continuous_effects is not None:
        try:
            source = resolution_effect_source(host, effect)
            if (
                "keywords" not in effect
                or power_delta
                or toughness_delta
            ):
                create_resolution_continuous_effect(
                    host,
                    source=source,
                    targets=affected,
                    layer=Layer.POWER_TOUGHNESS,
                    sublayer="7c",
                    operations=(
                        ContinuousOperation(
                            "modify_power_toughness",
                            [power_delta, toughness_delta],
                        ),
                    ),
                    expires_on_next_turn_of=expiry_player,
                )
            if keywords or fragments:
                create_resolution_continuous_effect(
                    host,
                    source=source,
                    targets=affected,
                    layer=Layer.ABILITY,
                    sublayer="6",
                    operations=(
                        *(ContinuousOperation("add_ability", keyword) for keyword in keywords),
                        *(ContinuousOperation("add_ability_fragment", fragment) for fragment in fragments),
                    ),
                    expires_on_next_turn_of=expiry_player,
                )
        except ContinuousEffectStateError as exc:
            raise GameRuleError(str(exc)) from exc
    else:
        for card in affected:
            until_end = card.annotations.setdefault(
                "until_end_of_turn", {}
            )
            until_end["power"] = (
                int(until_end.get("power", 0)) + power_delta
            )
            until_end["toughness"] = (
                int(until_end.get("toughness", 0)) + toughness_delta
            )
            card.temporary_keywords = unique_preserving_order(
                [*card.temporary_keywords, *keywords]
            )
    host._log(
        actor,
        str(effect.get("event_code") or "effect.mass_modify"),
        (
            f"Matching permanents got {power_delta:+d}/{toughness_delta:+d} "
            "until end of turn."
        ),
        {
            "amount": effect.get("amount"),
            "power_delta": power_delta,
            "toughness_delta": toughness_delta,
            "keywords": list(keywords),
            "objects": [
                host.state.cards[object_id].ref
                for object_id in affected_ids
            ],
        },
        importance=2,
        changed_objects=affected_ids,
    )
    return [
        host.state.cards[object_id].ref
        for object_id in affected_ids
    ]



def _apply_pump_controlled_creatures(
    host: Any,
    effect: Mapping[str, Any],
    *,
    actor: str,
    operation: str,
    reason: str,
) -> Any:
    op = operation
    amount = int(effect.get("amount", 0))
    minimum = int(effect.get("minimum_amount", 0))
    if amount < minimum:
        return []
    keywords = [
        str(value)
        for value in effect.get("keywords", [])
    ]
    predicate = ObjectQuerySpec(
        zones=("battlefield",),
        controller=actor,
        types_all=("creature",),
    )
    affected = matching_battlefield_objects(host, predicate)
    changed = [card.object_id for card in affected]
    if host.state.continuous_effects is not None:
        try:
            if amount:
                create_resolution_continuous_effect(
                    host,
                    source=resolution_effect_source(host, effect),
                    targets=affected,
                    layer=Layer.POWER_TOUGHNESS,
                    sublayer="7c",
                    operations=(
                        ContinuousOperation(
                            "modify_power_toughness", [amount, amount]
                        ),
                    ),
                )
            if keywords:
                create_resolution_continuous_effect(
                    host,
                    source=resolution_effect_source(host, effect),
                    targets=affected,
                    layer=Layer.ABILITY,
                    sublayer="6",
                    operations=tuple(
                        ContinuousOperation("add_ability", keyword)
                        for keyword in keywords
                    ),
                )
        except ContinuousEffectStateError as exc:
            raise GameRuleError(str(exc)) from exc
    else:
        for card in affected:
            until_end = card.annotations.setdefault(
                "until_end_of_turn", {}
            )
            until_end["power"] = int(
                until_end.get("power", 0)
            ) + amount
            until_end["toughness"] = int(
                until_end.get("toughness", 0)
            ) + amount
            card.temporary_keywords = unique_preserving_order(
                [*card.temporary_keywords, *keywords]
            )
    host._log(
        actor,
        "effect.creature_pump",
        f"{actor}'s creatures got +{amount}/+{amount}.",
        {
            "amount": amount,
            "keywords": keywords,
            "objects": [
                host.state.cards[object_id].ref
                for object_id in changed
            ],
        },
        importance=2,
        changed_objects=changed,
        changed_players=[actor],
    )
    return [
        host.state.cards[object_id].ref
        for object_id in changed
    ]



def _apply_shuffle_into_library(
    host: Any,
    effect: Mapping[str, Any],
    *,
    actor: str,
    operation: str,
    reason: str,
) -> Any:
    from ..rules.death_counter_shuffle import DEATH_COUNTER_SHUFFLE_FIELD, resolve_departure_shuffle

    if DEATH_COUNTER_SHUFFLE_FIELD in effect:
        departure = resolve_departure_shuffle(host.state, host.active_seats, effect)
        if departure is None:
            return None
        owner, card = departure
    else:
        card = host._resolve_object(actor, str(effect["card"]), zones={"battlefield", "graveyard", "exile", "stack"})
        owner = card.owner

    moved = None
    if card is not None:
        moved = host.move_card(
            card.object_id,
            "library",
            reason=reason,
            semantic_events=True,
            replacement_selections=tuple(effect.get("_replacement_selections") or ()),
        )
    host.shuffle_library(owner, reason=reason)
    return moved.ref if moved is not None else None



def _apply_shuffle_graveyard_bottom_random(
    host: Any,
    effect: Mapping[str, Any],
    *,
    actor: str,
    operation: str,
    reason: str,
) -> Any:
    op = operation
    raw_players = effect.get("players")
    players = (
        [str(value) for value in raw_players]
        if isinstance(raw_players, Sequence)
        and not isinstance(raw_players, (str, bytes))
        else (
            [str(raw_players)]
            if raw_players
            else []
        )
    )
    moved_by_player: dict[str, list[str]] = {}
    for seat in players:
        if seat not in host.active_seats:
            continue
        graveyard_ids = list(
            host.state.players[seat].zones["graveyard"]
        )
        if not graveyard_ids:
            moved_by_player[seat] = []
            continue
        randomizer = random.Random(
            f"{host.state.config.seed}|{host.state.turn_sequence}|"
            f"{seat}|graveyard-bottom|{host.state.event_sequence}"
        )
        randomizer.shuffle(graveyard_ids)
        host._move_cards_simultaneously(
            [
                (object_id, "library")
                for object_id in graveyard_ids
            ],
            reason=reason,
            log=False,
        )
        library = host.state.players[seat].zones["library"]
        moved_set = set(graveyard_ids)
        remaining_ids = [
            object_id
            for object_id in library
            if object_id not in moved_set
        ]
        library[:] = [*graveyard_ids, *remaining_ids]
        moved_by_player[seat] = [
            host.state.cards[object_id].ref
            for object_id in graveyard_ids
        ]
        host._log(
            actor,
            "graveyard.bottom",
            (
                f"{seat} put {len(graveyard_ids)} graveyard "
                "card(s) on the bottom in a random order."
            ),
            {
                "player": seat,
                "count": len(graveyard_ids),
                "reason": reason,
            },
            importance=2,
            changed_objects=graveyard_ids,
            changed_players=[seat],
        )
    return moved_by_player



def _apply_reveal_top_permanent(
    host: Any,
    effect: Mapping[str, Any],
    *,
    actor: str,
    operation: str,
    reason: str,
) -> Any:
    op = operation
    seat = str(effect.get("player") or actor)
    library = host.state.players[seat].zones["library"]
    if not library:
        return None
    card = host.state.cards[library[-1]]
    card.known_to = list(host.seats)
    card.revealed_to = list(host.seats)
    host._log(
        actor,
        "library.reveal",
        f"{seat} revealed {card.ref} {card.printed_name}.",
        {"player": seat, "object": card.ref},
        importance=2,
        changed_objects=[card.object_id],
    )
    data = host._effective_card_data(card)
    types, _, _ = host._type_parts(
        str(data.get("type_line") or "")
    )
    if types.intersection(
        {
            "artifact",
            "battle",
            "creature",
            "enchantment",
            "land",
            "planeswalker",
        }
    ):
        host.move_card(
            card.object_id,
            "battlefield",
            controller=seat,
            putting_player=seat,
            reason=reason,
            semantic_events=True,
        )
    return card.ref


HANDLERS = {
    'recover_source_result': apply_recover_source_result,
    'linked_exile_return': resolve_linked_exile_return,
    'return_linked_exiled_objects': return_linked_exiled_objects,
    'prepare_graveyard_creature_aura': _apply_prepare_graveyard_creature_aura,
    'reanimate_attached_creature_aura': _apply_reanimate_attached_creature_aura,
    'attach': _apply_attach_instruction,
    'unattach': _apply_unattach,
    'create_attached_token': _apply_create_attached_token,
    'bestow_prepare': _apply_bestow_prepare,
    'bounce': _apply_bounce_or_destroy_or_discard_or_exile_or_move_or_sacrifice,
    'destroy': _apply_bounce_or_destroy_or_discard_or_exile_or_move_or_sacrifice,
    'destroy_selected': _apply_destroy_selected,
    'discard': _apply_bounce_or_destroy_or_discard_or_exile_or_move_or_sacrifice,
    'exile': _apply_bounce_or_destroy_or_discard_or_exile_or_move_or_sacrifice,
    'exile_all': _apply_exile_all,
    'exile_graveyard': _apply_exile_graveyard,
    'exile_opponent_graveyards': _apply_exile_opponent_graveyards,
    'exile_top_library_card': apply_exile_top_library_card,
    'mill': _apply_mill,
    'move': _apply_bounce_or_destroy_or_discard_or_exile_or_move_or_sacrifice,
    'move_if_in_zone': apply_move_if_in_zone,
    'pump_controlled_creatures': _apply_pump_controlled_creatures,
    REANIMATE_OPERATION: wrap_gift_reanimation(_apply_reanimate),
    'reveal_top_permanent': _apply_reveal_top_permanent,
    'sacrifice': _apply_bounce_or_destroy_or_discard_or_exile_or_move_or_sacrifice,
    'shuffle_graveyard_bottom_random': _apply_shuffle_graveyard_bottom_random,
    'shuffle_into_library': _apply_shuffle_into_library,
    'modify_all_matching_permanents_until_end_of_turn': _apply_modify_all_matching_permanents_until_end_of_turn,
    'exchange_artifact_zones': _apply_exchange_artifact_zones,
}


def apply_effect(
    host: Any,
    effect: Mapping[str, Any],
    *,
    actor: str,
    operation: str,
    reason: str,
) -> Any:
    handler = HANDLERS.get(operation)
    if handler is None:
        raise GameRuleError(
            f"Unsupported owned effect {operation!r}"
        )
    return handler(
        host,
        effect,
        actor=actor,
        operation=operation,
        reason=reason,
    )
