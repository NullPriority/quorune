"""An Aura's original attachment and the ordered temporary control result."""

from __future__ import annotations

from collections.abc import Mapping

from ..attachment_references import (
    ATTACHMENT_INFORMATION_CAPABILITY, AttachmentReferenceKind, AttachmentReferenceSpec,
)
from ..keyword_abilities import FIXED_CHARACTERISTIC_KEYWORD_CAPABILITIES
from ..object_predicate import ObjectQuerySpec
from ..replacement.immutable import FrozenMap, thaw_value
from .effect_shape_walkers import nested_effects
from .event_subscriptions import FixedEventSubscription, fixed_event_subscription_condition


ATTACHED_TEMPORARY_CONTROL_VERSION = "attached_temporary_control_version"
ATTACHED_TEMPORARY_CONTROL_CAPABILITY = "continuous.control.attached_temporary_set"
ATTACHED_TEMPORARY_CONTROL_STEPS = ("gain_control", "untap", "haste")
ATTACHED_TEMPORARY_CONTROL_MECHANICS = (
    "fixed-resolution-control", "cr-611-continuous-effects", "tap-and-untap", "haste",
)
ATTACHED_CONTROL_RECEIVERS = ObjectQuerySpec(zones=("battlefield",))
_ATTACHMENT = AttachmentReferenceSpec(AttachmentReferenceKind.ENCHANTED, "permanent", schema_version=2)


def attached_temporary_control_condition():
    return fixed_event_subscription_condition((
        FixedEventSubscription("permanent.enter", {"field": "card", "op": "eq", "value": "$source.ref"}),
        FixedEventSubscription("step.begin", {"all": [
            {"field": "player", "op": "eq", "value": "$source.controller"},
            {"field": "step", "op": "eq", "value": "upkeep"},
        ]}),
    ))


def attached_temporary_control_effect():
    return {
        "op": "gain_control_set", ATTACHED_TEMPORARY_CONTROL_VERSION: 1,
        "card": _ATTACHMENT.to_dict(), "controller": "$controller", "source": "$source",
        "duration": "until_end_of_turn", "steps": list(ATTACHED_TEMPORARY_CONTROL_STEPS),
    }


def validate_attached_temporary_control(effect, *, actor="$controller", resolved=False):
    if (not isinstance(effect, Mapping) or type(effect.get(ATTACHED_TEMPORARY_CONTROL_VERSION)) is not int
            or effect[ATTACHED_TEMPORARY_CONTROL_VERSION] != 1):
        raise ValueError("Attached temporary control requires its exact descriptor version")
    expected = attached_temporary_control_effect()
    value = thaw_value(FrozenMap(effect))
    if not resolved:
        if value != expected or AttachmentReferenceSpec.from_dict(value["card"]) != _ATTACHMENT:
            raise ValueError("Attached temporary control requires its exact enchanted-permanent reference and order")
        return None
    target, source = value.get("card"), value.get("source")
    if (type(source) is not str or not source or source.startswith("$")
            or target is not None and (type(target) is not str or not target or target.startswith("$"))):
        raise ValueError("Attached temporary control changed its original resolving source or attachment")
    actual = {key: entry for key, entry in value.items() if key not in {"reason", "_runtime_source"}}
    if actual != {**expected, "card": target, "source": source, "controller": actor}:
        raise ValueError("Attached temporary control changed its original order, duration or controller")
    return target


def attached_temporary_control_node_capabilities(*, effects, target_schema):
    if len(effects) != 1 or target_schema is not None:
        return ()
    try:
        validate_attached_temporary_control(effects[0])
    except (TypeError, ValueError):
        return ()
    return (
        ATTACHED_TEMPORARY_CONTROL_CAPABILITY, ATTACHMENT_INFORMATION_CAPABILITY,
        "continuous.control.fixed_resolution", "continuous.resolution.fixed_characteristics_until_end_of_turn",
        "permanent.untap.effect", *FIXED_CHARACTERISTIC_KEYWORD_CAPABILITIES["Haste"],
    )


def attached_temporary_control_runtime_dependencies(program):
    if not any(ATTACHED_TEMPORARY_CONTROL_VERSION in effect for effect in nested_effects(
        (program.effects, program.handlers, program.target_schema),
    )):
        return ()
    required = attached_temporary_control_node_capabilities(effects=program.effects, target_schema=program.target_schema)
    if (not required or (program.event, program.active_zone) != ("permanent.enter", "battlefield")
            or thaw_value(program.event_condition) != attached_temporary_control_condition()
            or program.cost_schema is not None or program.handlers):
        raise ValueError("Attached temporary control requires both original independent Aura scopes")
    return required
