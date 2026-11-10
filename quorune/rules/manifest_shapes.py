from __future__ import annotations

from collections.abc import Mapping

from ..manifest import (
    MANIFEST_ATTACHMENT_CAPABILITY, MANIFEST_ATTACHMENT_RESULT, MANIFEST_ATTACHMENT_VERSION,
    MANIFEST_CAPABILITY, MANIFEST_DREAD_CAPABILITY, is_manifested,
)


def validate_manifest_instruction(effect, actor):
    fields = {"op", "player", "mode"}
    attaches = MANIFEST_ATTACHMENT_VERSION in effect
    if attaches:
        fields.add(MANIFEST_ATTACHMENT_VERSION)
    if (set(effect) != fields or effect.get("op") != "manifest_card"
            or effect.get("player") != actor or effect.get("mode") not in {"top", "dread"}):
        raise ValueError("Manifest needs one current controller and represented mode")
    if attaches and (effect["mode"] != "dread" or type(effect[MANIFEST_ATTACHMENT_VERSION]) is not int
                     or effect[MANIFEST_ATTACHMENT_VERSION] != 1):
        raise ValueError("Manifest attachment requires one versioned Dread result")
    return attaches


def manifest_attachment_payload_dependencies(effect):
    if MANIFEST_ATTACHMENT_RESULT in effect:
        raise ValueError("Manifest attachments require a committed Dread choice")
    if MANIFEST_ATTACHMENT_VERSION in effect:
        validate_manifest_instruction(effect, "$controller")
        return (MANIFEST_ATTACHMENT_CAPABILITY,)
    return ()


def validate_manifest_attachment_envelope(program):
    def contains_attachment(value):
        if isinstance(value, Mapping):
            return MANIFEST_ATTACHMENT_VERSION in value or any(contains_attachment(child) for child in value.values())
        return isinstance(value, (tuple, list)) and any(contains_attachment(child) for child in value)

    if contains_attachment(program.effects) and (
        program.event != "permanent.enter.self" or program.active_zone != "battlefield"
        or program.target_schema is not None or program.event_condition or len(program.effects) != 1
        or not validate_manifest_instruction(program.effects[0], "$controller")
    ):
        raise ValueError("Manifest attachment requires one original self-entry trigger")


def manifested_attachment_is_current(cards, effect):
    binding = effect[MANIFEST_ATTACHMENT_RESULT]
    if (set(effect) != {"op", "attachment_kind", "source", "target", MANIFEST_ATTACHMENT_RESULT}
            or effect.get("op") != "attach" or effect.get("attachment_kind") != "equipment"
            or not isinstance(binding, dict) or set(binding) != {"source", "target"}):
        raise ValueError("Manifest attachment requires both exact object incarnations")
    for role in ("source", "target"):
        identity, object_id = binding[role], effect[role]
        if (type(identity) is not str or type(object_id) is not str or not object_id
                or identity.rpartition("@")[0] != object_id or not identity.rpartition("@")[2].isdecimal()):
            raise ValueError("Manifest attachment incarnation is malformed")
    source, target = cards.get(effect["source"]), cards.get(effect["target"])
    return (source is not None and target is not None and source.zone == "battlefield"
            and not source.phased_out and not target.phased_out and is_manifested(target)
            and source.logical_object_id == binding["source"] and target.logical_object_id == binding["target"])


def manifest_node_capabilities(*, effects, target_schema, mechanics):
    if target_schema is not None or not effects or 'manifest' not in mechanics:
        return ()
    try:
        attached = [validate_manifest_instruction(effect, "$controller") for effect in effects]
    except (TypeError, ValueError, KeyError):
        return ()
    if any(attached) and len(effects) != 1:
        return ()
    deps = {MANIFEST_CAPABILITY}
    if any(attached):
        deps.add(MANIFEST_ATTACHMENT_CAPABILITY)
    if any(effect['mode'] == 'dread' for effect in effects):
        deps.add(MANIFEST_DREAD_CAPABILITY)
    if 'cr-603-handling-triggered-abilities' in mechanics:
        deps.add('trigger.placement.apnap')
    if 'trigger-event-normalized-zone-change' in mechanics:
        deps.add('trigger.event.normalized_zone_change')
    return tuple(sorted(deps))
