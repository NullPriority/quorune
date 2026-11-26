"""Prepare and commit one token creation event with distinct attached Roles."""

from collections import Counter
from collections.abc import Mapping
from dataclasses import replace

from ..ability_fragments import GrantedTriggeredAbilitySpec, StaticComponentSpec, canonical_ability_fragments
from ..replacement.immutable import FrozenMap, thaw_value


ATTACHED_ROLE_BATCH_OPERATION = "create_attached_role_batch"
ATTACHED_ROLE_BATCH_CAPABILITY = "token.creation.attached_role_batch"
ATTACHED_ROLE_BATCH_COMMIT = "_attached_role_batch_order"
PREVIEW_FIELD = "_attached_role_batch_preview"
_ROLE_TYPE = "Token Enchantment — Aura Role"
_FIELDS = {"op", "schema_version", "player", "stack_ref", "recipients", "role"}


def attached_role_batch_effect(role, *, actor, stack_ref, recipients):
    return {"op": ATTACHED_ROLE_BATCH_OPERATION, "schema_version": 1,
            "player": actor, "stack_ref": stack_ref,
            "recipients": [{"object_id": card.object_id, "ref": card.ref,
                            "logical_object_id": card.logical_object_id} for card in recipients],
            "role": thaw_value(role)}


def attached_role_batch_spec(effect, *, actor, stack_ref, commit=False):
    from .role_token_shapes import role_token_node_capabilities

    runtime = {"_replacement_selections"}
    if commit:
        runtime.update((ATTACHED_ROLE_BATCH_COMMIT, PREVIEW_FIELD))
    if (not isinstance(effect, Mapping) or not _FIELDS <= set(effect) <= _FIELDS | runtime
            or effect["op"] != ("create_token" if commit else ATTACHED_ROLE_BATCH_OPERATION)
            or type(effect["schema_version"]) is not int or effect["schema_version"] != 1
            or effect["player"] != actor or effect["stack_ref"] != stack_ref
            or any(type(value) is not str or not value or value.startswith("$") for value in (actor, stack_ref))
            or not isinstance(effect.get("_replacement_selections", ()), (list, tuple))):
        raise ValueError("An attached Role batch requires its original resolving controller and stack object")
    recipients = effect["recipients"]
    if (not isinstance(recipients, (list, tuple)) or not recipients
            or any(not isinstance(row, Mapping) or set(row) != {"object_id", "ref", "logical_object_id"}
                   or any(type(value) is not str or not value for value in row.values()) for row in recipients)
            or any(len({row[key] for row in recipients}) != len(recipients)
                   for key in ("object_id", "ref", "logical_object_id"))):
        raise ValueError("Attached Role recipients need distinct exact physical arrivals")
    role = effect["role"]
    if (not isinstance(role, Mapping) or role.get("controller") != actor
            or role.get("aura_target_ref") is not None or role.get("name") != "Wicked"
            or "aura_target_ref" not in role):
        raise ValueError("An attached Role batch needs the versioned Wicked Role result")
    printed = {**thaw_value(role), "controller": "$controller", "aura_target_ref": "$source.zone_object"}
    if not role_token_node_capabilities(effects=(printed,), target_schema=None, mechanics=("predefined-role-token",)):
        raise ValueError("An attached Role batch needs its complete predefined enchantment")
    return recipients, role


def _current_creature(host, row):
    card = host.state.cards.get(row["object_id"])
    if (card is not None and card.zone == "battlefield" and not card.phased_out
            and card.ref == row["ref"] and card.logical_object_id == row["logical_object_id"]
            and "creature" in host._type_parts(str(host._effective_card_data(card).get("type_line") or ""))[0]):
        return card
    return None


def resolve_attached_role_batch(host, actor, effect, *, commit=False):
    from ..token_creation import (
        ResolvedTokenSpecs, _preflight_aura_token_specs, _resolved_token_specs,
        _token_replacement_effects, _token_specification_inputs,
    )

    stack_ref = effect.get("stack_ref")
    item = next((value for value in host.state.stack if value.ref == stack_ref), None)
    if (item is None or item.controller != actor or item.context.get("currently_resolving") is not True):
        raise ValueError("Attached Role creation must resume its original resolving object")
    recipients, role = attached_role_batch_spec(effect, actor=actor, stack_ref=stack_ref, commit=commit)
    living = tuple(card for row in recipients if (card := _current_creature(host, row)) is not None)
    if not living:
        return ResolvedTokenSpecs(specs=(), journal=(), remaining_selections=()), 0
    for fragment in canonical_ability_fragments(role["characteristics"]["ability_fragments"]):
        if isinstance(fragment, (StaticComponentSpec, GrantedTriggeredAbilitySpec)):
            child = host.semantics.get(fragment.semantic_key)
            if (child is None or not child.provenance.get("granted_only")
                    or not host.semantic_program_is_current_trusted(child)):
                raise ValueError("An attached Role child is missing its original trusted definition")
    base, types, subtypes, sources = _token_specification_inputs(
        host, actor, name=role["name"], quantity=1, tapped=False,
        attacking=None, attacking_groups=(), battle_protector=None, copy_of=None, copy_source_zone="battlefield",
        copy_snapshot=None, characteristics=thaw_value(role["characteristics"]),
        temporary_keywords=(), aura_target_ref=None,
    )
    specifications = tuple({**base[0], "aura_target_ref": card.ref} for card in living)
    eligible = _preflight_aura_token_specs(host, actor, specifications)
    if not eligible:
        return ResolvedTokenSpecs(specs=(), journal=(), remaining_selections=()), 0
    replacements = _token_replacement_effects(host, actor, types, subtypes, sources)
    result = _resolved_token_specs(
        host, actor, quantity=len(eligible), token_specs=eligible, created_types=types, created_subtypes=subtypes,
        replacement_effects=replacements, replacement_selections=tuple(effect.get("_replacement_selections", ())),
    )
    after = _preflight_aura_token_specs(host, actor, result.specs)
    return replace(result, specs=after), len(eligible)


def role_slots(preview):
    slots = []
    index = 0
    for token in preview:
        count = token.get("quantity")
        if type(count) is not int or count < 0:
            raise ValueError("Attached Role replacement has an invalid token count")
        target = token.get("aura_target_ref")
        if token.get("characteristics", {}).get("type_line") == _ROLE_TYPE and target is not None:
            slots.extend((index + offset, target) for offset in range(count))
        index += count
    return tuple(slots)


def role_order_required(slots):
    return any(count > 1 for count in Counter(target for _, target in slots).values())


def commit_attached_role_batch(host, effect, *, actor, reason):
    from ..token_creation import TokenCreationError, _commit_resolved_token_specs

    try:
        resolved, quantity = resolve_attached_role_batch(host, actor, effect, commit=True)
        preview = tuple(FrozenMap(token) for token in resolved.specs)
        if preview != tuple(FrozenMap(token) for token in effect[PREVIEW_FIELD]):
            raise ValueError("Attached Role replacements changed before commit")
        proposed = tuple(effect[ATTACHED_ROLE_BATCH_COMMIT])
        slots = role_slots(preview)
        expected = {index for index, _ in slots}
        if (any(type(index) is not int for index in proposed)
                or len(proposed) != len(set(proposed)) or set(proposed) != expected):
            raise ValueError("Attached Role ordering must name exactly the attached Roles")
    except (KeyError, TypeError, ValueError) as exc:
        raise TokenCreationError(str(exc)) from exc
    return _commit_resolved_token_specs(
        host, actor, resolved=resolved, base_name="Wicked", base_quantity=quantity, reason=reason,
        role_timestamp_order=proposed or None, mixed_role_timestamps=True,
    )
