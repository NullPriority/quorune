from __future__ import annotations

"""Intervening condition for the source Aura's current greatest-power creature."""

from collections.abc import Mapping
from dataclasses import dataclass

from ..attachment_references import (
    ATTACHMENT_INFORMATION_CAPABILITY, ATTACHMENT_INFORMATION_VERSION,
    AttachmentReferenceKind, AttachmentReferenceSpec, resolve_source_attachment,
)
from ..attachments import attached_object_identity
from ..continuous_conditions import (
    FIXED_PUBLIC_STATE_INTERVENING_CONDITION_FIELD, FIXED_PUBLIC_STATE_INTERVENING_COVERAGE,
)
from ..public_characteristic_aggregates import greatest_power_creatures
from ..replacement.immutable import FrozenMap, thaw_value


ATTACHED_GREATEST_POWER_KIND = "attached_creature_has_greatest_power"
ATTACHED_GREATEST_POWER_CAPABILITIES = (
    "trigger.condition.fixed_public_state",
    ATTACHMENT_INFORMATION_CAPABILITY,
    "quantity_expression.public_characteristic_aggregate",
)
_ATTACHMENT = AttachmentReferenceSpec(AttachmentReferenceKind.ENCHANTED, "creature", schema_version=2)


@dataclass(frozen=True, slots=True)
class AttachedGreatestPowerCondition:
    def to_dict(self) -> dict:
        return {"schema_version": 1, "kind": ATTACHED_GREATEST_POWER_KIND, "attachment": _ATTACHMENT.to_dict()}

    @classmethod
    def from_dict(cls, value: Mapping) -> "AttachedGreatestPowerCondition":
        result = cls()
        if (not is_attached_greatest_power(value) or type(value.get("schema_version")) is not int
                or thaw_value(FrozenMap(value)) != result.to_dict()):
            raise ValueError("The maximum-power trigger requires its exact source attachment condition")
        return result


def is_attached_greatest_power(value) -> bool:
    return isinstance(value, Mapping) and value.get("kind") == ATTACHED_GREATEST_POWER_KIND


def attached_greatest_power_event_condition(event_condition) -> AttachedGreatestPowerCondition | None:
    if not isinstance(event_condition, Mapping):
        return None
    values = event_condition.get("all", (event_condition,))
    if not isinstance(values, (list, tuple)):
        return None
    found = [item for item in values if isinstance(item, Mapping) and is_attached_greatest_power(item.get("condition"))]
    if not found:
        return None
    if len(found) != 1:
        raise ValueError("One fixed trigger may carry only one attached maximum predicate")
    atom = found[0]
    if (set(atom) != {"field", "op", "value", "condition"}
            or atom["field"] != FIXED_PUBLIC_STATE_INTERVENING_CONDITION_FIELD
            or atom["op"] != "truthy" or atom["value"] is not True):
        raise ValueError("The attached maximum requires the ordinary intervening predicate")
    return AttachedGreatestPowerCondition.from_dict(atom["condition"])


def attached_greatest_power_runtime_dependencies(program) -> tuple[str, ...]:
    if attached_greatest_power_event_condition(program.event_condition) is None:
        return ()
    expected = ({"op": "draw", "player": "$controller", "count": 1, "private": True},)
    if (program.event != "permanent.enter.self" or program.active_zone != "battlefield"
            or program.target_schema is not None
            or not {"intervening_condition", FIXED_PUBLIC_STATE_INTERVENING_COVERAGE} <= set(program.coverage)
            or tuple(program.effects) != expected or type(program.effects[0]["count"]) is not int
            or program.effects[0]["private"] is not True):
        raise ValueError("The attached maximum requires the exact source-entry private draw")
    return ATTACHED_GREATEST_POWER_CAPABILITIES


def _has_maximum_creature_power(host, target) -> bool:
    if target is None:
        return False
    rows = tuple(host._public_object_query_result(card, _maximum_layer=None)
                 for card in host.state.cards.values() if card.zone == "battlefield" and not card.phased_out)
    return any(row.logical_object_id == target.logical_object_id for row in greatest_power_creatures(rows))


def current_attached_greatest_power_holds(host, source) -> bool:
    live = host.state.cards.get(source.object_id)
    if live is None or live.zone != "battlefield" or live.logical_object_id != source.logical_object_id:
        return False
    identity = attached_object_identity(host.state.cards, live)
    return _has_maximum_creature_power(host, host.state.cards.get(identity.object_id) if identity else None)


def recheck_attached_greatest_power(host, context, *, controller, source_object_id) -> bool:
    version = context.get(ATTACHMENT_INFORMATION_VERSION)
    if (type(version) is not int or version != 1 or context.get("event") != "permanent.enter"
            or type(controller) is not str or controller not in host.active_seats
            or context.get("intervening_condition_controller") != controller):
        raise ValueError("The attached maximum lost its original entry, controller or departure information")
    target = resolve_source_attachment(
        host.state.cards, context.get("source_attachment_snapshot"), _ATTACHMENT,
        source_object_id=source_object_id, source_logical_object_id=context.get("source_logical_object_id"),
    )
    return _has_maximum_creature_power(host, target)
