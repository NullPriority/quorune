from __future__ import annotations

"""Current-or-immediate-LKI copy references behind the canonical zone hook."""

from copy import deepcopy
from typing import Any, Mapping, Sequence

from .token_copy_recipes import TokenCopyRecipeError, TokenCopyRecipeSpec, copy_recipes


COPY_REFERENCE_CONTEXT = "token_copy_references_v1"


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
    if not any(recipe.origin == "source" for recipe in copy_recipes(effects)):
        return {}
    return {COPY_REFERENCE_CONTEXT: {"source": _snapshot(host, source)}}


def _attachment_identity(host: Any, item: Any) -> tuple[str, str, str] | None:
    """Read the canonical relation, keeping the original source incarnation."""
    from .attachment_references import (
        ATTACHMENT_INFORMATION_VERSION, AttachmentReferenceKind,
        AttachmentReferenceSpec, SourceAttachmentSnapshot, resolve_source_attachment,
    )
    version = item.context.get(ATTACHMENT_INFORMATION_VERSION)
    if type(version) is not int or version != 1:
        raise TokenCopyRecipeError("Enchanted copy requires versioned attachment information")
    snapshot = SourceAttachmentSnapshot.from_dict(item.context.get("source_attachment_snapshot"))
    source_id = item.source_object_id or item.card_object_id
    logical_id = item.context.get("source_logical_object_id")
    spec = AttachmentReferenceSpec(AttachmentReferenceKind.ENCHANTED, "creature", schema_version=2)
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


def _identity(host: Any, item: Any, origin: str, *, target_index=None) -> tuple[str, str, str] | None:
    if target_index is not None and (origin!='target' or type(target_index) is not int or not 0<=target_index<len(item.targets)):
        raise TokenCopyRecipeError('Copy target index is invalid')
    if origin == "source_attachment":
        return _attachment_identity(host, item)
    context = item.context.get(COPY_REFERENCE_CONTEXT)
    key=origin if target_index is None else f'target:{target_index}'
    cached = context.get(key) if isinstance(context, Mapping) else None
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


def copy_reference_snapshot(host: Any, item: Any, recipe: TokenCopyRecipeSpec, *, target_index=None) -> Mapping[str, Any] | None:
    if recipe.origin == "event_spell":
        if target_index is not None:
            raise TokenCopyRecipeError("The original cast spell is not a selected target")
        return _cast_spell_token_reference(host,item)
    identity = _identity(host, item, recipe.origin,target_index=target_index)
    if identity is None and recipe.origin == "source_attachment":
        return None
    if identity is None:
        raise TokenCopyRecipeError("Copy reference identity is unavailable")
    object_id, logical_object_id, zone = identity
    card = host.state.cards.get(object_id)
    if card is not None and card.logical_object_id == logical_object_id and card.zone == zone:
        value = _snapshot(host, card)
    else:
        key=recipe.origin if target_index is None else f'target:{target_index}'
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
    if not isinstance(value, Mapping) or set(value) != {"object_id", "logical_object_id", "ref", "zone", "snapshot"}:
        raise TokenCopyRecipeError("Copy reference snapshot is unavailable or malformed")
    return deepcopy(dict(value))


def pin_copy_characteristic_departures(host: Any, cards: Sequence[Any], *, error_type=TokenCopyRecipeError) -> None:
    """Only evaluate references belonging to this committed departure group."""
    departing = {card.object_id: card for card in cards if card.zone == "battlefield"}
    if not departing:
        return
    updates = []
    try:
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
                recipe=TokenCopyRecipeSpec.from_dict(effect['copy_spec'])
                target=effect.get('_copy_target');index=target['index'] if isinstance(target,Mapping) else None
                identity = _identity(host, item, recipe.origin,target_index=index)
                if identity is None:continue
                object_id,logical_object_id,zone=identity
                card=departing.get(object_id)
                if card is None or zone!='battlefield' or card.logical_object_id!=logical_object_id:continue
                key=recipe.origin if index is None else f'target:{index}'
                updates.append((item,key,_snapshot(host,card)))
        for item, origin, value in updates:
            item.context.setdefault(COPY_REFERENCE_CONTEXT, {})[origin] = value
    except (KeyError, TypeError, ValueError) as error:
        raise error_type(str(error)) from error
