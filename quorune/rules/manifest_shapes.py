from __future__ import annotations

from collections.abc import Mapping

from ..manifest import (
    MANIFEST_ATTACHMENT_CAPABILITY, MANIFEST_ATTACHMENT_RESULT, MANIFEST_ATTACHMENT_VERSION,
    MANIFEST_CAPABILITY, MANIFEST_COUNTERS_CAPABILITY, MANIFEST_COUNTERS_FIELD, MANIFEST_COUNTERS_OPERATION,
    MANIFEST_DREAD_CAPABILITY, MANIFEST_TOP_TWO_CAPABILITY, is_manifested,
)
from ..query_effect_amount_model import CastXAmountSpec, DECLARED_AMOUNT_CAPABILITY
from ..shared_type_doors import DOOR_UNLOCKED_EVENT


def validate_manifest_instruction(effect, actor, *, resolved=False):
    fields = {"op", "player", "mode"}
    attaches = MANIFEST_ATTACHMENT_VERSION in effect
    counters = MANIFEST_COUNTERS_FIELD in effect
    if attaches:
        fields.add(MANIFEST_ATTACHMENT_VERSION)
    if counters:
        fields.add(MANIFEST_COUNTERS_FIELD)
    if (set(effect) != fields or effect.get("op") != "manifest_card"
            or effect.get("player") != actor or type(effect.get("mode")) is not str
            or effect["mode"] not in {"top", "top_two", "dread"}):
        raise ValueError("Manifest needs one current controller and represented mode")
    if attaches and (effect["mode"] != "dread" or type(effect[MANIFEST_ATTACHMENT_VERSION]) is not int
                     or effect[MANIFEST_ATTACHMENT_VERSION] != 1):
        raise ValueError("Manifest attachment requires one versioned Dread result")
    if counters:
        spec = effect[MANIFEST_COUNTERS_FIELD]
        if (attaches or effect["mode"] == "top_two" or not isinstance(spec, Mapping)
                or set(spec) != {"schema_version", "count"} or type(spec["schema_version"]) is not int
                or spec["schema_version"] != 1):
            raise ValueError("Manifest counters require one versioned creature result")
        count = spec["count"]
        if not resolved and isinstance(count, Mapping) and effect["mode"] == "top":
            if CastXAmountSpec.from_dict(count).coefficient != 1:
                raise ValueError("Manifest counters use the announced X")
        elif type(count) is not int or (count < 0 if resolved else count not in {1, 2, 3}):
            raise ValueError("Manifest counter count is malformed")
    return attaches


def manifest_result_payload_dependencies(effect):
    if MANIFEST_ATTACHMENT_RESULT in effect or effect.get("op") == MANIFEST_COUNTERS_OPERATION:
        raise ValueError("Manifest follow-ups require a committed choice")
    if (MANIFEST_ATTACHMENT_VERSION in effect or MANIFEST_COUNTERS_FIELD in effect
            or (effect.get("op") == "manifest_card" and effect.get("mode") == "top_two")):
        return _manifest_effect_capabilities(effect)
    return ()


def validate_manifest_result_envelope(program):
    def contains(value, field):
        if isinstance(value, Mapping):
            return field in value or any(contains(child, field) for child in value.values())
        return isinstance(value, (tuple, list)) and any(contains(child, field) for child in value)

    if contains(program.effects, MANIFEST_ATTACHMENT_VERSION) and (
        program.event != "permanent.enter.self" or program.active_zone != "battlefield"
        or program.target_schema is not None or program.event_condition or len(program.effects) != 1
        or not validate_manifest_instruction(program.effects[0], "$controller")
    ):
        raise ValueError("Manifest attachment requires one original self-entry trigger")
    if contains(program.effects, MANIFEST_COUNTERS_FIELD):
        effects = program.effects
        if (len(effects) != 1 or MANIFEST_COUNTERS_FIELD not in effects[0] or program.target_schema is not None):
            raise ValueError("Manifest counters require one complete untargeted instruction")
        effect, = effects
        validate_manifest_instruction(effect, "$controller")
        if (program.event, program.active_zone) == ("resolve", "stack") and not program.event_condition:
            return
        if ((program.event, program.active_zone) != (DOOR_UNLOCKED_EVENT, "battlefield") or effect["mode"] != "dread"):
            raise ValueError("Manifest counters require a spell or an unlocked Room door")


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


def _manifest_effect_capabilities(effect):
    attaches = validate_manifest_instruction(effect, "$controller")
    deps = {MANIFEST_CAPABILITY}
    if attaches:
        deps.add(MANIFEST_ATTACHMENT_CAPABILITY)
    if effect["mode"] == "dread":
        deps.add(MANIFEST_DREAD_CAPABILITY)
    if effect["mode"] == "top_two":
        deps.add(MANIFEST_TOP_TWO_CAPABILITY)
    if MANIFEST_COUNTERS_FIELD in effect:
        deps.add(MANIFEST_COUNTERS_CAPABILITY)
        if isinstance(effect[MANIFEST_COUNTERS_FIELD]["count"], Mapping):
            deps.add(DECLARED_AMOUNT_CAPABILITY)
    return tuple(sorted(deps))


def manifest_node_capabilities(*, effects, target_schema, mechanics):
    if target_schema is not None or not effects or 'manifest' not in mechanics:
        return ()
    try:
        deps = {cap for effect in effects for cap in _manifest_effect_capabilities(effect)}
    except (TypeError, ValueError, KeyError):
        return ()
    if len(effects) != 1 and deps & {MANIFEST_ATTACHMENT_CAPABILITY, MANIFEST_COUNTERS_CAPABILITY, MANIFEST_TOP_TWO_CAPABILITY}:
        return ()
    if 'cr-603-handling-triggered-abilities' in mechanics:
        deps.add('trigger.placement.apnap')
    if 'trigger-event-normalized-zone-change' in mechanics:
        deps.add('trigger.event.normalized_zone_change')
    return tuple(sorted(deps))
