from __future__ import annotations

"""Current-or-immediate-LKI copy references behind the canonical zone hook."""

from copy import deepcopy
from dataclasses import dataclass, replace
from typing import Any, Mapping, Sequence

from .attachment_references import AttachmentObjectIdentity
from .replacement.immutable import FrozenMap, thaw_value
from .rules.attached_copy_payment import FALLBACK_FIELD, attached_copy_payment_parts
from .token_copy_recipes import GRAVEYARD_TARGET_TOKEN_COPY_ORIGIN, TokenCopyRecipeError, TokenCopyRecipeSpec, copy_recipes
from .token_copy_recipient_model import has_occurrence_copy_recipient
from .rules.token_copy_recipients import represents_occurrence_controller_copy
from .rules.cast_target_copy import (
    CAST_TARGET_COPY_ORIGIN, controlled_cast_copy_target, represents_cast_target_copy,
    validate_cast_target_copy_reference,
)


COPY_REFERENCE_CONTEXT = "token_copy_references_v1"
ATTACHED_COPY_PAYMENT_CONTEXT = "attached_copy_payment_v1"


def _snapshot(host: Any, card: Any) -> dict[str, Any]:
    from .token_creation import token_copy_snapshot
    if card.phased_out:
        raise TokenCopyRecipeError("Copy reference is phased out")
    from .face_down_characteristics_model import face_down_characteristics_mode
    if card.face_down and face_down_characteristics_mode(host)!=1:
        raise TokenCopyRecipeError("Face-down copiable characteristics are not represented by this recipe")
    return {"object_id": card.object_id, "logical_object_id": card.logical_object_id,
            "ref": card.ref, "zone": card.zone, "snapshot": token_copy_snapshot(host, card)}


def copy_source_context(host: Any, source: Any, effects: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    recipes = copy_recipes(effects)
    result = {}
    if any(recipe.origin == "source" for recipe in recipes):
        result[COPY_REFERENCE_CONTEXT] = {"source": _snapshot(host, source)}
    if any(recipe.source_bound_cleanup for recipe in recipes):
        result["source_logical_object_id"] = source.logical_object_id
    return result


def triggered_copy_reference_context(host, program, event, context):
    from .rules.event_spell_copy import event_spell_copy_trigger_context
    from .rules.temporary_occurrence_copy import represents_temporary_occurrence_copy

    retained = event_spell_copy_trigger_context(host, program, event, context)
    if any(represents_cast_target_copy(effect) for effect in program.effects):
        card = controlled_cast_copy_target(host, context, controller=context.get("controller"))
        if event != "spell.cast" or card is None:
            raise TokenCopyRecipeError("Cast target copy lost its matching original target")
        return {COPY_REFERENCE_CONTEXT: {"event_object": _snapshot(host, card)}}
    if not (represents_temporary_occurrence_copy(program.effects) or represents_occurrence_controller_copy(program.effects)):
        return retained
    logical_id = context.get("card_object_identity")
    object_id = logical_id.rpartition("@")[0] if type(logical_id) is str else ""
    card = host.state.cards.get(object_id)
    if (event != "creature.enter" or card is None or card.ref != context.get("card")
            or card.logical_object_id != logical_id or card.zone != "battlefield"):
        raise TokenCopyRecipeError("Occurrence copy lost its original entering incarnation")
    return {**retained, COPY_REFERENCE_CONTEXT: {"event_object": _snapshot(host, card)}}


def source_attachment_copy_identity(host: Any, item: Any, attachment_kind: str) -> tuple[str, str, str] | None:
    """Read the canonical relation, keeping the original source incarnation."""
    from .attachment_references import (
        ATTACHMENT_INFORMATION_VERSION, AttachmentReferenceKind,
        AttachmentReferenceSpec, SourceAttachmentSnapshot, resolve_source_attachment,
    )
    version = item.context.get(ATTACHMENT_INFORMATION_VERSION)
    if type(version) is not int or version != 1:
        raise TokenCopyRecipeError("Attached copy requires versioned attachment information")
    snapshot = SourceAttachmentSnapshot.from_dict(item.context.get("source_attachment_snapshot"))
    source_id = item.source_object_id or item.card_object_id
    logical_id = item.context.get("source_logical_object_id")
    relation = AttachmentReferenceKind.EQUIPPED if attachment_kind == "equipped" else AttachmentReferenceKind.ENCHANTED
    spec = AttachmentReferenceSpec(relation, "creature", schema_version=2)
    # This validates the source identity even when the referenced creature left.
    current = resolve_source_attachment(
        host.state.cards, snapshot.to_dict(), spec,
        source_object_id=source_id, source_logical_object_id=logical_id,
    )
    if current is not None:
        return current.object_id, current.logical_object_id, "battlefield"
    source = host.state.cards.get(source_id)
    if source is not None and source.logical_object_id == logical_id:
        return None
    identity = snapshot.attached_object
    return ((identity.object_id, identity.logical_object_id, "battlefield")
            if identity is not None else None)


@dataclass(frozen=True, slots=True)
class _DepartedAttachmentModel:
    identity: AttachmentObjectIdentity
    controller: str
    creature: bool

    def __post_init__(self):
        if (not isinstance(self.identity, AttachmentObjectIdentity) or type(self.controller) is not str
                or not self.controller or self.controller.startswith("$") or type(self.creature) is not bool):
            raise ValueError("Attachment departure needs a closed public creature and controller observation")

    def to_dict(self):
        return {"identity": self.identity.to_dict(), "controller": self.controller, "creature": self.creature}

    @classmethod
    def from_dict(cls, raw):
        if not isinstance(raw, Mapping) or set(raw) != {"identity", "controller", "creature"}:
            raise ValueError("Attachment departure observation fields changed")
        return cls(AttachmentObjectIdentity.from_dict(raw["identity"]), raw["controller"], raw["creature"])


@dataclass(frozen=True, slots=True)
class _AttachedCopySeal:
    qualified: AttachmentObjectIdentity | None = None
    departure: _DepartedAttachmentModel | None = None

    def to_dict(self):
        return {"schema_version": 1,
                "qualified": self.qualified.to_dict() if self.qualified is not None else None,
                "departure": self.departure.to_dict() if self.departure is not None else None}

    @classmethod
    def from_item(cls, item):
        raw = item.context.get(ATTACHED_COPY_PAYMENT_CONTEXT)
        if raw is None and ATTACHED_COPY_PAYMENT_CONTEXT not in item.context:
            return cls()
        if (not isinstance(raw, Mapping) or set(raw) != {"schema_version", "qualified", "departure"}
                or type(raw["schema_version"]) is not int or raw["schema_version"] != 1):
            raise ValueError("Attached copy seal has a closed version-one schema")
        qualified = AttachmentObjectIdentity.from_dict(raw["qualified"]) if raw["qualified"] is not None else None
        departure = _DepartedAttachmentModel.from_dict(raw["departure"]) if raw["departure"] is not None else None
        return cls(qualified, departure)


def _paid_attachment_copy_identity(item, *, required=False):
    model = _AttachedCopySeal.from_item(item).qualified
    if required and model is None:
        raise ValueError("Paid attachment copy requires a sealed recipient")
    return (model.object_id, model.logical_object_id, "battlefield") if model is not None else None


def _current_attached_model(host, card):
    identity = AttachmentObjectIdentity(card.object_id, card.logical_object_id, card.ref)
    effective = host._effective_card_data(card)
    types, _, _ = host._type_parts(str(effective.get("type_line") or ""))
    return _DepartedAttachmentModel(identity, effective.get("controller", card.controller), "creature" in types)


def _observe_attachment_copy_departure(host, item, card):
    seal = _AttachedCopySeal.from_item(item)
    if seal.qualified is not None and card.logical_object_id != seal.qualified.logical_object_id:
        raise ValueError("Attached copy managed departure changed the qualified incarnation")
    return replace(seal, departure=_current_attached_model(host, card)).to_dict()


def bind_attached_copy_payment(host, item, raw):
    """Qualify the original attachment recipient before mana can move it."""
    _, copy, fallback = attached_copy_payment_parts(raw)
    program = host.semantics.get(item.semantic_key)
    if (program is None or len(program.effects) != 1 or FrozenMap(program.effects[0]) != FrozenMap(raw)
            or not host.semantic_program_is_current_trusted(program)):
        raise ValueError("Attached copy payment requires its trusted resolving ability")
    actor = item.controller
    fallback = {**fallback, "controller": actor}
    identity = source_attachment_copy_identity(host, item, "enchanted")
    if identity is None:
        return fallback
    object_id, logical_id, _ = identity
    seal = _AttachedCopySeal.from_item(item)
    if seal.qualified is not None:
        raise ValueError("Attached copy payment cannot qualify its recipient twice")
    card = host.state.cards.get(object_id)
    if card is not None and card.zone == "battlefield" and card.logical_object_id == logical_id:
        if card.phased_out:
            return fallback
        model = _current_attached_model(host, card)
    else:
        model = seal.departure
    if model is None or (model.identity.object_id, model.identity.logical_object_id) != (object_id, logical_id):
        raise ValueError("Attached copy payment lacks the original recipient's public departure facts")
    if not model.creature or model.controller != actor:
        return fallback
    item.context[ATTACHED_COPY_PAYMENT_CONTEXT] = replace(seal, qualified=model.identity).to_dict()
    copy = thaw_value(FrozenMap(copy))

    return {**thaw_value(FrozenMap(raw)), "player": actor, "source": model.identity.reference,
            "effects": [{**copy, "controller": actor, FALLBACK_FIELD: fallback}],
            "declined_effects": [fallback]}


def _copy_reference_key(origin, target_index):
    if origin == CAST_TARGET_COPY_ORIGIN:
        return "event_object"
    return origin if target_index is None else f"target:{target_index}"


def _current_graveyard_copy_target_identity(host, item, *, target_index):
    if target_index is not None or len(item.targets) != 1:
        raise TokenCopyRecipeError("A graveyard copy requires its one original target slot")
    ref = item.targets[0]
    selected = item.context.get("target_snapshots", {}).get(ref)
    if not isinstance(selected, Mapping) or selected.get("zone") != "graveyard":
        return None
    card = host.state.cards.get(selected.get("object_id"))
    if (card is None or card.ref != ref or card.zone != "graveyard" or card.owner != item.controller
            or card.zone_change_counter != selected.get("zone_change_counter") or not card.is_card_object):
        return None
    data = host._effective_card_data(card)
    if "creature" not in host._type_parts(str(data.get("type_line") or ""))[0]:
        return None
    return card.object_id, card.logical_object_id, "graveyard"


def _identity(host: Any, item: Any, origin: str, *, target_index=None, attachment_kind=None) -> tuple[str, str, str] | None:
    if origin == GRAVEYARD_TARGET_TOKEN_COPY_ORIGIN:
        return _current_graveyard_copy_target_identity(host, item, target_index=target_index)
    if target_index is not None and (origin!='target' or type(target_index) is not int or not 0<=target_index<len(item.targets)):
        raise TokenCopyRecipeError('Copy target index is invalid')
    if origin == "source_attachment":
        return source_attachment_copy_identity(host, item, attachment_kind or "enchanted")
    context = item.context.get(COPY_REFERENCE_CONTEXT)
    key = _copy_reference_key(origin, target_index)
    cached = context.get(key) if isinstance(context, Mapping) else None
    if origin == CAST_TARGET_COPY_ORIGIN:
        validate_cast_target_copy_reference(item, cached)
    if isinstance(cached,Mapping) and target_index is not None:
        ref=item.targets[target_index]
        target=item.context.get('target_snapshots',{}).get(ref)
        if (not isinstance(target,Mapping) or cached.get('ref')!=ref
                or cached.get('object_id')!=target.get('object_id')
                or cached.get('logical_object_id')!=f"{target.get('object_id')}@{target.get('zone_change_counter')}"):
            cached=None
    if isinstance(cached, Mapping):
        return cached.get("object_id"), cached.get("logical_object_id"), cached.get("zone")
    if origin == "source":
        card = host.state.cards.get(item.source_object_id or item.card_object_id or "")
        expected = item.context.get("source_logical_object_id")
        if card is not None and card.logical_object_id == expected:
            return card.object_id, expected, "battlefield"
    if origin == "target" and (len(item.targets) == 1 or target_index is not None):
        index=0 if target_index is None else target_index
        if type(index) is not int or not 0<=index<len(item.targets):raise TokenCopyRecipeError('Copy target index is invalid')
        ref = item.targets[index]
        target = item.context.get("target_snapshots", {}).get(ref)
        card = next((value for value in host.state.cards.values() if value.ref == ref), None)
        if isinstance(target, Mapping) and card is not None and card.zone_change_counter == target.get("zone_change_counter"):
            return card.object_id, card.logical_object_id, "battlefield"
    if origin == "event_object":
        context = item.context.get("event_context", item.context)
        card = next((value for value in host.state.cards.values() if value.ref == context.get("card")), None)
        if card is not None and context.get("card_object_identity"):
            return card.object_id, context["card_object_identity"], "battlefield"
    return None


def _cast_spell_token_reference(host, trigger):
    """Adapt the exact public cast observation to the existing token identity owner."""
    from .entry_copy import COPIABLE_ORACLE_FIELD
    from .event_spell_copy_model import CAST_SPELL_TOKEN_ORACLE_CONTEXT, EVENT_SPELL_COPY_CONTEXT
    from .rules.event_spell_copy import decode_event_spell_copy_source, resolve_event_spell_copy_source
    from .replacement.immutable import thaw_value
    from .characteristic_evaluation import type_parts

    occurrence = trigger.context.get("event_context", trigger.context)
    raw = trigger.context.get(EVENT_SPELL_COPY_CONTEXT)
    original = decode_event_spell_copy_source(raw)
    spell = original.item()
    if (spell.kind != "spell" or not isinstance(occurrence, Mapping)
            or occurrence.get("stack") != spell.ref
            or occurrence.get("logical_object_id") != original.logical_object_id
            or trigger.source_object_id != spell.card_object_id):
        raise TokenCopyRecipeError("Direct token creation needs the original cast source")
    resolved = resolve_event_spell_copy_source(host, {"stack": spell.ref, "copy_source": raw})
    values = thaw_value(resolved.characteristics)
    types, _subtypes, _supertypes = type_parts(values["type_line"])
    if (not types.intersection({"artifact","battle","creature","enchantment","planeswalker"})
            or types.intersection({"instant","sorcery","land"})):
        raise TokenCopyRecipeError("Direct token creation requires a represented permanent spell")
    captured_id = trigger.context.get(CAST_SPELL_TOKEN_ORACLE_CONTEXT)
    oracle_id = values.get(COPIABLE_ORACLE_FIELD, captured_id)
    name = values.get("name")
    if (type(captured_id) is not str or not captured_id
            or type(oracle_id) is not str or not oracle_id or type(name) is not str or not name):
        raise TokenCopyRecipeError("Cast spell copy requires exact copiable identity values")
    return {"object_id":spell.card_object_id,"logical_object_id":resolved.logical_object_id,
            "ref":spell.ref,"zone":"stack",
            "snapshot":{"oracle_id":oracle_id,"printed_name":name,"characteristics":values,
                        "annotations":{"copied_from":spell.card_object_id,"copy_overrides":values}}}


def copy_reference_snapshot(host: Any, item: Any, recipe: TokenCopyRecipeSpec, *, target_index=None,
                            use_phased_lki=False, paid_attachment=False) -> Mapping[str, Any] | None:
    if recipe.origin == "event_spell":
        if target_index is not None:
            raise TokenCopyRecipeError("The original cast spell is not a selected target")
        return _cast_spell_token_reference(host,item)
    paid_ref = None
    if paid_attachment:
        identity = _paid_attachment_copy_identity(item, required=True)
        paid_ref = _AttachedCopySeal.from_item(item).qualified.reference
    else:
        identity = _identity(host, item, recipe.origin, target_index=target_index, attachment_kind=recipe.attachment_kind)
    if identity is None and recipe.origin in {"source_attachment", GRAVEYARD_TARGET_TOKEN_COPY_ORIGIN}:
        return None
    if identity is None:
        raise TokenCopyRecipeError("Copy reference identity is unavailable")
    object_id, logical_object_id, zone = identity
    card = host.state.cards.get(object_id)
    if (card is not None and card.logical_object_id == logical_object_id and card.zone == zone
            and not ((use_phased_lki or recipe.origin == CAST_TARGET_COPY_ORIGIN) and card.phased_out)):
        value = _snapshot(host, card)
    else:
        key = _copy_reference_key(recipe.origin, target_index)
        value = item.context.get(COPY_REFERENCE_CONTEXT, {}).get(key)
        if recipe.origin == "source_attachment" and isinstance(value, Mapping):
            if (value.get("object_id"), value.get("logical_object_id"), value.get("zone")) != identity:
                value = None
        if value is None and recipe.origin == "event_object":
            context = item.context.get("event_context", item.context)
            snapshot = context.get("copiable_snapshot")
            if isinstance(snapshot, Mapping):
                value = {"object_id": object_id, "logical_object_id": logical_object_id,
                         "ref": context["card"], "zone": zone, "snapshot": snapshot}
    if (not isinstance(value, Mapping) or set(value) != {"object_id", "logical_object_id", "ref", "zone", "snapshot"}
            or paid_ref is not None and paid_ref != value["ref"]):
        raise TokenCopyRecipeError("Copy reference snapshot is unavailable or malformed")
    return deepcopy(dict(value))


def _pin_original_occurrence_copy_departures(host, departing):
    from .rules.temporary_occurrence_copy import represents_temporary_occurrence_copy
    from .trigger_batches import PendingTriggerBatch
    from .trigger_processing import replace_pending_trigger_batch

    frames = [(item.ref, item.semantic_key, item.context) for item in host.state.stack]
    frames.extend((item.ref, item.payload.get("semantic_key"), item.payload.get("context", {}))
                  for batch in host.state.pending_trigger_batches for item in batch.items)
    updates, snapshots = {}, {}
    for ref, semantic_key, context in frames:
        references = context.get(COPY_REFERENCE_CONTEXT)
        saved = references.get("event_object") if isinstance(references, Mapping) else None
        if not isinstance(saved, Mapping):
            continue
        card = departing.get(saved.get("object_id"))
        if card is None or card.phased_out or card.logical_object_id != saved.get("logical_object_id"):
            continue
        program = host.semantics.get(semantic_key) if semantic_key else None
        if program is None or not (
            represents_temporary_occurrence_copy(program.effects)
            or represents_occurrence_controller_copy(program.effects)
            or any(represents_cast_target_copy(effect) for effect in program.effects)
        ):
            continue
        if card.ref != saved.get("ref") or saved.get("zone") != "battlefield":
            raise TokenCopyRecipeError("Occurrence copy reference changed its original incarnation")
        if card.object_id not in snapshots:
            snapshots[card.object_id] = _snapshot(host, card)
        updates[ref] = snapshots[card.object_id]
    if not updates:
        return
    for item in host.state.stack:
        if item.ref in updates:
            item.context[COPY_REFERENCE_CONTEXT]["event_object"] = deepcopy(updates[item.ref])
    for index, batch in enumerate(host.state.pending_trigger_batches):
        data = batch.to_dict()
        changed = False
        for group in data["groups"]:
            for item in group["items"]:
                if item["ref"] in updates:
                    item["context"][COPY_REFERENCE_CONTEXT]["event_object"] = deepcopy(updates[item["ref"]])
                    changed = True
        if changed:
            replace_pending_trigger_batch(host, index, PendingTriggerBatch.from_dict(data))


def _pin_pending_attached_copy_departures(host, departing):
    from .model import StackItem
    from .rules.attached_copy_payment import uses_attached_copy_payment
    from .trigger_batches import PendingTriggerBatch
    from .trigger_processing import replace_pending_trigger_batch

    for index, batch in enumerate(host.state.pending_trigger_batches):
        data = batch.to_dict()
        changed = False
        for group in data["groups"]:
            for frame in group["items"]:
                program = host.semantics.get(frame.get("semantic_key"))
                if (program is None or len(program.effects) != 1
                        or not uses_attached_copy_payment(program.effects[0])):
                    continue
                item = StackItem.from_dict(frame)
                identity = source_attachment_copy_identity(host, item, "enchanted")
                if identity is None:
                    continue
                object_id, logical_id, zone = identity
                model = departing.get(object_id)
                if model is None or zone != "battlefield" or model.logical_object_id != logical_id:
                    continue
                context = frame["context"]
                context.setdefault(COPY_REFERENCE_CONTEXT, {})["source_attachment"] = _snapshot(host, model)
                context[ATTACHED_COPY_PAYMENT_CONTEXT] = _observe_attachment_copy_departure(host, item, model)
                changed = True
        if changed:
            replace_pending_trigger_batch(host, index, PendingTriggerBatch.from_dict(data))


def pin_copy_characteristic_departures(host: Any, cards: Sequence[Any], *, error_type=TokenCopyRecipeError,
                                       phase_out=False) -> None:
    """Only evaluate references belonging to this committed departure group."""
    departing = {card.object_id: card for card in cards if card.zone == "battlefield"}
    if not departing:
        return
    updates = []
    try:
        _pin_original_occurrence_copy_departures(host, departing)
        if phase_out:
            from .target_controller_information import pin_occurrence_controller_departures

            pin_occurrence_controller_departures(host, tuple(departing.values()), phase_out=True)
            return
        _pin_pending_attached_copy_departures(host, departing)
        for item in host.state.stack:
            program = host.semantics.get(item.semantic_key) if item.semantic_key else None
            if program is None:
                continue
            effects=program.effects
            from .targets import mode_effects,has_scoped_modal_targets
            schema=getattr(program,'target_schema',None)
            if has_scoped_modal_targets(schema):
                effects=mode_effects(schema,item.modes,target_groups=item.context.get('target_groups') or item.context.get('target_groups_current') or {})
            from .token_copy_recipes import executable_copy_effects
            for effect in executable_copy_effects(effects):
                if has_occurrence_copy_recipient(effect) or represents_cast_target_copy(effect):
                    continue
                recipe=TokenCopyRecipeSpec.from_dict(effect['copy_spec'])
                target=effect.get('_copy_target');index=target['index'] if isinstance(target,Mapping) else None
                scoped = FALLBACK_FIELD in effect
                qualified = _paid_attachment_copy_identity(item) if scoped else None
                identity = qualified or _identity(
                    host, item, recipe.origin, target_index=index, attachment_kind=recipe.attachment_kind,
                )
                if identity is None:continue
                object_id,logical_object_id,zone=identity
                card=departing.get(object_id)
                if card is None or zone!='battlefield' or card.logical_object_id!=logical_object_id:continue
                key=recipe.origin if index is None else f'target:{index}'
                observation = _observe_attachment_copy_departure(host, item, card) if scoped else None
                updates.append((item, key, _snapshot(host, card), observation))
        for item, origin, value, observation in updates:
            item.context.setdefault(COPY_REFERENCE_CONTEXT, {})[origin] = value
            if observation is not None:
                item.context[ATTACHED_COPY_PAYMENT_CONTEXT] = observation
    except (KeyError, TypeError, ValueError) as error:
        raise error_type(str(error)) from error
