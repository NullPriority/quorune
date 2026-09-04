from __future__ import annotations

import re
from typing import Any, Mapping, Protocol

from ..attachment_references import (
    AttachmentReferenceError,
    AttachmentReferenceSpec,
    resolve_source_attachment,
)
from ..station import StationAbilityError, station_resolution_power

from .context import SemanticNodeError
from .explore import explore_source_controller


_INDEX_GROUP = "index"


class SemanticValueHost(Protocol):
    state: Any

    def _stack_source_ref(self, item: Any) -> str | None: ...

    def _target_snapshot(self, target_ref: str) -> dict[str, Any]: ...

    def _type_parts(
        self, type_line: str
    ) -> tuple[set[str], set[str], set[str]]: ...


def _resolve_attachment_reference(
    host: SemanticValueHost,
    value: Mapping[str, Any],
    item: Any,
) -> str | None:
    spec = AttachmentReferenceSpec.from_dict(value)
    snapshot = item.context.get("source_attachment_snapshot")
    if not isinstance(snapshot, Mapping):
        raise AttachmentReferenceError(
            "Attachment-reference stack context is missing or malformed"
        )
    source_object_id = item.source_object_id or item.card_object_id
    source_logical_object_id = item.context.get("source_logical_object_id")
    if type(source_object_id) is not str or not source_object_id:
        raise AttachmentReferenceError(
            "Attachment-reference stack source identity is missing"
        )
    if (
        type(source_logical_object_id) is not str
        or not source_logical_object_id
    ):
        raise AttachmentReferenceError(
            "Attachment-reference stack source incarnation is missing"
        )
    target = resolve_source_attachment(
        host.state.cards,
        snapshot,
        spec,
        source_object_id=source_object_id,
        source_logical_object_id=source_logical_object_id,
    )
    if target is None:
        return None
    card_types = host._type_parts(
        str(host._target_snapshot(target.ref).get("type_line") or "")
    )[0]
    if (
        spec.required_card_type != "permanent"
        and spec.required_card_type not in card_types
    ):
        return None
    return target.ref


def resolve_semantic_value(
    host: SemanticValueHost,
    value: Any,
    item: Any,
) -> Any:
    """Resolve transport-safe runtime placeholders against one stack item."""

    if isinstance(value, list):
        return [resolve_semantic_value(host, child, item) for child in value]
    if isinstance(value, Mapping) and value.get("kind") == "source_attachment":
        return _resolve_attachment_reference(host, value, item)
    if isinstance(value, dict):
        return {
            key: resolve_semantic_value(host, child, item)
            for key, child in value.items()
        }
    if not isinstance(value, str) or not value.startswith("$"):
        return value
    if value == "$controller":
        return item.controller
    if value == "$active":
        return host.state.active_player
    if value == "$source":
        return host._stack_source_ref(item)
    if value == "$source.zone_object":
        object_id = str(getattr(item, "source_object_id", None) or "")
        logical_object_id = str(
            getattr(item, "context", {}).get("source_logical_object_id")
            or ""
        )
        source = host.state.cards.get(object_id)
        if (
            source is None
            or source.zone != "battlefield"
            or source.phased_out
            or not logical_object_id
            or source.logical_object_id != logical_object_id
        ):
            return None
        return source.ref
    if value == "$source.transform_count":
        count = getattr(item, "context", {}).get("source_transform_count")
        if type(count) is not int or count < 0:
            return None
        return count
    if value == "$source.controller":
        return explore_source_controller(item, host.state.cards)
    if value == "$card":
        card = host.state.cards.get(item.card_object_id or "")
        return card.ref if card else None
    if value == "$stack":
        return item.ref
    if value == "$x":
        return item.x_value or 0
    if value == "$turn_sequence":
        return host.state.turn_sequence
    if value == "$station.power":
        try:
            return station_resolution_power(host, item)
        except StationAbilityError as exc:
            raise SemanticNodeError(str(exc)) from exc
    if value.startswith("$context."):
        return item.context.get(value.removeprefix("$context."))
    if value == "$targets":
        return [target for target in item.targets if target is not None]
    attribute_match = re.fullmatch(
        r"\$target\.(?P<attribute>controller|owner|mana_value|colors|type_line)"
        r"[.\[](?P<index>\d+)\]?",
        value,
    )
    if attribute_match:
        index = int(attribute_match.group(_INDEX_GROUP))
        if index >= len(item.targets):
            return None
        target_ref = item.targets[index]
        if target_ref is None:
            return None
        snapshot = dict(
            item.context.get("target_snapshots", {}).get(
                str(target_ref),
                host._target_snapshot(str(target_ref)),
            )
        )
        return snapshot.get(attribute_match.group("attribute"))
    target_match = re.fullmatch(r"\$target[.\[](?P<index>\d+)\]?", value)
    if target_match:
        index = int(target_match.group(_INDEX_GROUP))
        if index >= len(item.targets):
            return None
        return item.targets[index]
    return value


__all__ = ["SemanticValueHost", "resolve_semantic_value"]
