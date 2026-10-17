from __future__ import annotations

"""Strict structural validation shared by independent direct-target owners."""

from dataclasses import dataclass
from typing import Any, Mapping

from ..replacement.immutable import FrozenMap, ImmutableValueError, freeze_value
from .context import ReadOnlyHandlerContext, SemanticNodeError
from ..selection.target_query import target_identity_matches_snapshot


_REASON_FIELD = "reason"

# Scalar recipients being mutated, rather than objects read for information.
_MUTATING_TARGET_FIELDS = {
    **{operation: 'card' for operation in (
        'destroy', 'bounce', 'exile_permanent', 'tap', 'untap', 'place_counters',
        'remove_counters', 'remove_all_counters', 'gain_control', 'regenerate',
        'sacrifice', 'apply_source_characteristics_until_end_of_turn',
    )},
    'damage': 'target',
}


def bound_mutation_target_overrides(host, effect, item, resolve_reference):
    """Omit mutations of a bound incarnation lost during earlier instructions."""
    field = _MUTATING_TARGET_FIELDS.get(effect.get('op'))
    reference = effect.get(field) if field is not None else None
    if not isinstance(reference, str) or not reference.startswith('$target'):
        return {}
    ref = resolve_reference(reference)
    if not isinstance(ref, str):
        return {}
    snapshot = item.context.get('target_snapshots', {}).get(ref)
    if (not isinstance(snapshot, Mapping) or snapshot.get('object_id') is None
            or snapshot.get('zone_change_counter') is None):
        return {}
    if not target_identity_matches_snapshot(host, ref, snapshot):
        return {field: None}
    return {}


@dataclass(frozen=True, slots=True)
class DirectTargetFields:
    object_ref: str
    reason: str
    replacement_selections: tuple[str | FrozenMap, ...] = ()


def bound_graveyard_return_target_snapshot(effect, item, resolve_reference):
    """Retain the original target identity across resolution-time prefixes."""
    if effect.get('op') != 'return_graveyard_card_to_owner_hand':
        return {}
    reference = effect.get('card')
    if not isinstance(reference, str) or not reference.startswith('$target'):
        return {}
    ref = resolve_reference(reference)
    if not isinstance(ref, str):
        return {}
    snapshot = item.context.get('target_snapshots', {}).get(ref)
    if not isinstance(snapshot, Mapping) or snapshot.get('object_id') is None or snapshot.get('zone_change_counter') is None:
        return {}
    return {'target_snapshot':{'ref':ref,'object_id':snapshot['object_id'],
        'zone_change_counter':snapshot['zone_change_counter']}}


def validate_direct_target_effect(
    effect: Mapping[str, Any],
    context: ReadOnlyHandlerContext,
    *,
    operation: str,
    reference_field: str,
    family_label: str,
    allow_replacement_selections: bool,
    additional_allowed_fields: tuple[str, ...] = (),
) -> DirectTargetFields:
    if any(
        type(field) is not str or not field
        for field in additional_allowed_fields
    ):
        raise SemanticNodeError(
            f"{family_label} additional fields must be nonempty strings"
        )
    allowed = {
        "op",
        reference_field,
        _REASON_FIELD,
        *additional_allowed_fields,
    }
    if allow_replacement_selections:
        allowed.add("_replacement_selections")
    unknown = sorted(set(effect) - allowed)
    if unknown:
        raise SemanticNodeError(
            f"{family_label} effect has unknown fields: " + ", ".join(unknown)
        )
    if effect.get("op") != operation:
        raise SemanticNodeError(f"{family_label} operation is unsupported")
    object_ref = effect.get(reference_field)
    if type(object_ref) is not str or not object_ref:
        raise SemanticNodeError(
            f"{family_label} effects require one nonempty target reference"
        )
    raw_reason = effect.get(_REASON_FIELD)
    if raw_reason is not None and (type(raw_reason) is not str or not raw_reason):
        raise SemanticNodeError(
            f"{family_label} effect reason must be a nonempty string"
        )
    raw_selections = effect.get("_replacement_selections", ())
    if raw_selections is None:
        raw_selections = ()
    if not allow_replacement_selections and raw_selections:
        raise SemanticNodeError(
            f"{family_label} effects do not accept replacement selections"
        )
    if not isinstance(raw_selections, (list, tuple)):
        raise SemanticNodeError(
            f"{family_label} replacement selections must be an array"
        )
    selections: list[str | FrozenMap] = []
    for index, selection in enumerate(raw_selections):
        if type(selection) is str:
            if not selection:
                raise SemanticNodeError(
                    f"{family_label} replacement selection {index} is empty"
                )
            selections.append(selection)
            continue
        if not isinstance(selection, Mapping):
            raise SemanticNodeError(
                f"{family_label} replacement selection {index} must be an object"
            )
        try:
            frozen = freeze_value(
                selection,
                field=f"{family_label} replacement selection {index}",
            )
        except ImmutableValueError as exc:
            raise SemanticNodeError(str(exc)) from exc
        if not isinstance(frozen, FrozenMap):
            raise SemanticNodeError(
                f"{family_label} replacement selection {index} is invalid"
            )
        selections.append(frozen)
    return DirectTargetFields(
        object_ref=object_ref,
        reason=raw_reason or context.default_reason,
        replacement_selections=tuple(selections),
    )


__all__ = ["DirectTargetFields", "validate_direct_target_effect"]
