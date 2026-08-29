from __future__ import annotations

from dataclasses import replace
from typing import Any, Mapping, Sequence

from ..affected_permanents import (
    AffectedPermanentSetError,
    AffectedPermanentSetSpec,
)
from ..entry_counter_model import EntryCounterError, EffectEntryCounter
from ..replacement.immutable import FrozenMap, thaw_value
from ..semantic_runtime import (
    CounterPlacementAmount,
    CreateTokenIntent,
    LifeChangeIntent,
    LibrarySelectionIntent,
    MoveObjectsSimultaneouslyIntent,
    PlaceCounterBatchIntent,
    PlaceCountersIntent,
    PlaceCountersOnSetIntent,
    PlaceCountersOnTargetsIntent,
    PlacePlayerCountersIntent,
    ProliferateIntent,
    ProliferateSubject,
    SurveilLibraryIntent,
    ZoneMoveIntent,
)
from ..rules.library_surveillance import (
    SurveilArrangement,
    SurveilError,
    SurveilObjectIdentity,
)
from ..rules.library_selection import (
    LibrarySelectionArrangement,
    LibrarySelectionError,
    LibrarySelectionObjectIdentity,
)
from .model import SemanticChoiceError


_REASON_FIELD = "reason"
_COUNTER_INTENT_FIELDS = {
    "actor",
    "object_refs",
    "counter_name",
    "amount",
    _REASON_FIELD,
    "source_ref",
}
_COUNTER_BATCH_INTENT_FIELDS = {
    "actor",
    "object_ref",
    "placements",
    _REASON_FIELD,
    "source_ref",
}
_COUNTER_BATCH_ENTRY_FIELDS = {"counter", "amount"}
_COUNTER_SET_INTENT_FIELDS = {
    "actor",
    "spec",
    "counter_name",
    "amount",
    _REASON_FIELD,
    "source_ref",
}
_COUNTER_TARGET_SET_INTENT_FIELDS = {
    "actor",
    "object_refs",
    "maximum_targets",
    "counter_name",
    "amount",
    _REASON_FIELD,
    "source_ref",
}
_PLAYER_COUNTER_INTENT_FIELDS = {
    "actor",
    "player_ids",
    "counter_name",
    "amount",
    _REASON_FIELD,
    "source_ref",
}
_ZONE_MOVE_FIELDS = {
    "actor",
    "object_ref",
    "expected_zones",
    "destination",
    _REASON_FIELD,
    "required_types",
    "owned_only",
    "controlled_only",
    "new_controller",
    "tapped_policy",
    "semantic_events",
    "optional_if_missing",
}
_ZONE_MOVE_EFFECT_ENTRY_FIELDS = _ZONE_MOVE_FIELDS | {
    "expected_zone_change_counter",
    "effect_entry_counters",
}
_SIMULTANEOUS_MOVE_FIELDS = {
    "actor",
    "object_refs",
    "expected_zones",
    "destination",
    _REASON_FIELD,
    "owned_only",
    "controlled_only",
}
_PROLIFERATE_FIELDS = {
    "actor",
    "subjects",
    _REASON_FIELD,
    "source_ref",
}
_PROLIFERATE_SUBJECT_FIELDS = {
    "subject_kind",
    "subject_id",
    "ref",
    "counter_names",
    "logical_object_id",
}
_CREATE_TOKEN_FIELDS = {
    "actor",
    "controller",
    "name",
    "quantity",
    _REASON_FIELD,
    "characteristics",
    "copy_of",
    "temporary_keywords",
    "sacrifice_at_end_step",
    "sacrifice_on_controller_end_step",
}
_LIFE_CHANGE_FIELDS = {
    "actor",
    "player",
    "amount",
    _REASON_FIELD,
    "source_ref",
}
_SURVEIL_FIELDS = {
    "actor",
    "player",
    "arrangement",
    "requested_count",
    _REASON_FIELD,
}
_SURVEIL_ARRANGEMENT_FIELDS = {
    "looked",
    "top_top_first",
    "graveyard_refs",
}
_LIBRARY_SELECTION_FIELDS = {
    "actor",
    "player",
    "arrangement",
    _REASON_FIELD,
    "source_stack_ref",
    "looked_are_public",
    "selected_are_public",
}
_LIBRARY_SELECTION_ARRANGEMENT_FIELDS = {
    "looked",
    "selected_refs",
    "remainder_refs",
    "remainder_destination",
    "remainder_order",
}


def counter_intent_identity(intent: PlaceCountersIntent) -> dict[str, Any]:
    if not isinstance(intent, PlaceCountersIntent):
        raise SemanticChoiceError(
            "Counter continuation requires a typed placement intent"
        )
    return {
        "actor": intent.actor,
        "object_refs": list(intent.object_refs),
        "counter_name": intent.counter_name,
        "amount": intent.amount,
        _REASON_FIELD: intent.reason,
        "source_ref": intent.source_ref,
    }


def validate_counter_intent_identity(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise SemanticChoiceError("Counter intent identity must be an object")
    actual = set(value)
    if actual != _COUNTER_INTENT_FIELDS:
        missing = sorted(_COUNTER_INTENT_FIELDS - actual)
        unknown = sorted(actual - _COUNTER_INTENT_FIELDS)
        details = [
            *(f"missing {name}" for name in missing),
            *(f"unknown {name}" for name in unknown),
        ]
        raise SemanticChoiceError(
            "Counter intent identity fields: " + "; ".join(details)
        )
    actor = value["actor"]
    refs = value["object_refs"]
    name = value["counter_name"]
    amount = value["amount"]
    reason = value[_REASON_FIELD]
    source = value["source_ref"]
    if (
        not isinstance(actor, str)
        or not actor
        or not isinstance(refs, (list, tuple))
        or not refs
        or any(not isinstance(ref, str) or not ref for ref in refs)
        or len(refs) != len(set(refs))
        or not isinstance(name, str)
        or not name
        or type(amount) is not int
        or amount < 0
        or not isinstance(reason, str)
        or (source is not None and (not isinstance(source, str) or not source))
    ):
        raise SemanticChoiceError("Counter intent identity is malformed")
    return {
        "actor": actor,
        "object_refs": list(refs),
        "counter_name": name,
        "amount": amount,
        _REASON_FIELD: reason,
        "source_ref": source,
    }


def _surveil_intent_identity(
    intent: SurveilLibraryIntent,
) -> tuple[str, dict[str, Any]]:
    return (
        "surveil_library",
        {
            "actor": intent.actor,
            "player": intent.player,
            "arrangement": {
                "looked": [
                    identity.to_dict()
                    for identity in intent.arrangement.looked
                ],
                "top_top_first": list(intent.arrangement.top_top_first),
                "graveyard_refs": list(intent.arrangement.graveyard_refs),
            },
            "requested_count": intent.requested_count,
            _REASON_FIELD: intent.reason,
        },
    )


def _library_selection_intent_identity(
    intent: LibrarySelectionIntent,
) -> tuple[str, dict[str, Any]]:
    return (
        "library_selection",
        {
            "actor": intent.actor,
            "player": intent.player,
            "arrangement": {
                "looked": [
                    identity.to_dict()
                    for identity in intent.arrangement.looked
                ],
                "selected_refs": list(intent.arrangement.selected_refs),
                "remainder_refs": list(intent.arrangement.remainder_refs),
                "remainder_destination": (
                    intent.arrangement.remainder_destination
                ),
                "remainder_order": intent.arrangement.remainder_order,
            },
            _REASON_FIELD: intent.reason,
            "source_stack_ref": intent.source_stack_ref,
            "looked_are_public": intent.looked_are_public,
            "selected_are_public": intent.selected_are_public,
        },
    )


def _simultaneous_move_intent_identity(
    intent: MoveObjectsSimultaneouslyIntent,
) -> tuple[str, dict[str, Any]]:
    return (
        "move_objects_simultaneously",
        {
            "actor": intent.actor,
            "object_refs": list(intent.object_refs),
            "expected_zones": list(intent.expected_zones),
            "destination": intent.destination,
            _REASON_FIELD: intent.reason,
            "owned_only": intent.owned_only,
            "controlled_only": intent.controlled_only,
        },
    )


def semantic_intent_identity(intent: Any) -> tuple[str, dict[str, Any]]:
    """Return the closed identity of a replacement-capable typed intent."""

    if isinstance(intent, LifeChangeIntent):
        return (
            "life_change",
            {
                "actor": intent.actor,
                "player": intent.player,
                "amount": intent.amount,
                _REASON_FIELD: intent.reason,
                "source_ref": intent.source_ref,
            },
        )
    if isinstance(intent, PlaceCountersIntent):
        return "place_counters", counter_intent_identity(intent)
    if isinstance(intent, PlaceCounterBatchIntent):
        return (
            "place_counter_batch",
            {
                "actor": intent.actor,
                "object_ref": intent.object_ref,
                "placements": [
                    {
                        "counter": placement.counter_name,
                        "amount": placement.amount,
                    }
                    for placement in intent.placements
                ],
                _REASON_FIELD: intent.reason,
                "source_ref": intent.source_ref,
            },
        )
    if isinstance(intent, PlaceCountersOnSetIntent):
        return (
            "place_counters_on_set",
            {
                "actor": intent.actor,
                "spec": intent.spec.to_dict(),
                "counter_name": intent.counter_name,
                "amount": intent.amount,
                _REASON_FIELD: intent.reason,
                "source_ref": intent.source_ref,
            },
        )
    if isinstance(intent, PlaceCountersOnTargetsIntent):
        return (
            "place_counters_on_targets",
            {
                "actor": intent.actor,
                "object_refs": list(intent.object_refs),
                "maximum_targets": intent.maximum_targets,
                "counter_name": intent.counter_name,
                "amount": intent.amount,
                _REASON_FIELD: intent.reason,
                "source_ref": intent.source_ref,
            },
        )
    if isinstance(intent, PlacePlayerCountersIntent):
        return (
            "place_player_counters",
            {
                "actor": intent.actor,
                "player_ids": list(intent.player_ids),
                "counter_name": intent.counter_name,
                "amount": intent.amount,
                _REASON_FIELD: intent.reason,
                "source_ref": intent.source_ref,
            },
        )
    if isinstance(intent, ProliferateIntent):
        return (
            "proliferate",
            {
                "actor": intent.actor,
                "subjects": [
                    {
                        "subject_kind": subject.subject_kind,
                        "subject_id": subject.subject_id,
                        "ref": subject.ref,
                        "counter_names": list(subject.counter_names),
                        "logical_object_id": subject.logical_object_id,
                    }
                    for subject in intent.subjects
                ],
                _REASON_FIELD: intent.reason,
                "source_ref": intent.source_ref,
            },
        )
    if isinstance(intent, CreateTokenIntent):
        return (
            "create_token",
            {
                "actor": intent.actor,
                "controller": intent.controller,
                "name": intent.name,
                "quantity": intent.quantity,
                _REASON_FIELD: intent.reason,
                "characteristics": thaw_value(intent.characteristics),
                "copy_of": intent.copy_of,
                "temporary_keywords": list(intent.temporary_keywords),
                "sacrifice_at_end_step": intent.sacrifice_at_end_step,
                "sacrifice_on_controller_end_step": (
                    intent.sacrifice_on_controller_end_step
                ),
            },
        )
    if isinstance(intent, ZoneMoveIntent):
        identity = {
            "actor": intent.actor,
            "object_ref": intent.object_ref,
            "expected_zones": list(intent.expected_zones),
            "destination": intent.destination,
            _REASON_FIELD: intent.reason,
            "required_types": list(intent.required_types),
            "owned_only": intent.owned_only,
            "controlled_only": intent.controlled_only,
            "new_controller": intent.new_controller,
            "tapped_policy": intent.tapped_policy,
            "semantic_events": intent.semantic_events,
            "optional_if_missing": intent.optional_if_missing,
        }
        if (
            intent.expected_zone_change_counter is not None
            or intent.effect_entry_counters
        ):
            identity.update(
                {
                    "expected_zone_change_counter": (
                        intent.expected_zone_change_counter
                    ),
                    "effect_entry_counters": [
                        counter.to_dict()
                        for counter in intent.effect_entry_counters
                    ],
                }
            )
        return (
            "zone_move",
            identity,
        )
    if isinstance(intent, MoveObjectsSimultaneouslyIntent):
        return _simultaneous_move_intent_identity(intent)
    if isinstance(intent, SurveilLibraryIntent):
        return _surveil_intent_identity(intent)
    if isinstance(intent, LibrarySelectionIntent):
        return _library_selection_intent_identity(intent)
    raise SemanticChoiceError(
        "Semantic replacement continuation requires a supported typed intent"
    )


def _string_sequence(value: Any, *, field_name: str) -> list[str]:
    if (
        not isinstance(value, (list, tuple))
        or any(type(item) is not str or not item for item in value)
        or len(value) != len(set(value))
    ):
        raise SemanticChoiceError(f"{field_name} must be unique strings")
    return list(value)


def _validate_player_counter_intent_identity(
    value: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise SemanticChoiceError(
            "Player counter intent identity must be an object"
        )
    actual = set(value)
    if actual != _PLAYER_COUNTER_INTENT_FIELDS:
        missing = sorted(_PLAYER_COUNTER_INTENT_FIELDS - actual)
        unknown = sorted(actual - _PLAYER_COUNTER_INTENT_FIELDS)
        details = [
            *(f"missing {name}" for name in missing),
            *(f"unknown {name}" for name in unknown),
        ]
        raise SemanticChoiceError(
            "Player counter intent identity fields: " + "; ".join(details)
        )
    actor = value["actor"]
    players = value["player_ids"]
    counter_name = value["counter_name"]
    amount = value["amount"]
    reason = value[_REASON_FIELD]
    source = value["source_ref"]
    if (
        type(actor) is not str
        or not actor
        or not isinstance(players, (list, tuple))
        or not players
        or any(type(player) is not str or not player for player in players)
        or len(players) != len(set(players))
        or type(counter_name) is not str
        or not counter_name
        or type(amount) is not int
        or amount <= 0
        or type(reason) is not str
        or not reason
        or (source is not None and (type(source) is not str or not source))
    ):
        raise SemanticChoiceError(
            "Player counter intent identity is malformed"
        )
    return {
        "actor": actor,
        "player_ids": list(players),
        "counter_name": counter_name,
        "amount": amount,
        _REASON_FIELD: reason,
        "source_ref": source,
    }


def _validate_counter_set_intent_identity(
    value: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise SemanticChoiceError(
            "Counter-set intent identity must be an object"
        )
    actual = set(value)
    if actual != _COUNTER_SET_INTENT_FIELDS:
        missing = sorted(_COUNTER_SET_INTENT_FIELDS - actual)
        unknown = sorted(actual - _COUNTER_SET_INTENT_FIELDS)
        details = [
            *(f"missing {name}" for name in missing),
            *(f"unknown {name}" for name in unknown),
        ]
        raise SemanticChoiceError(
            "Counter-set intent identity fields: " + "; ".join(details)
        )
    try:
        spec = AffectedPermanentSetSpec.from_dict(value["spec"])
    except (AffectedPermanentSetError, TypeError) as exc:
        raise SemanticChoiceError(
            "Counter-set intent specification is malformed"
        ) from exc
    actor = value["actor"]
    counter_name = value["counter_name"]
    amount = value["amount"]
    reason = value[_REASON_FIELD]
    source = value["source_ref"]
    if (
        type(actor) is not str
        or not actor
        or type(counter_name) is not str
        or not counter_name
        or type(amount) is not int
        or amount <= 0
        or type(reason) is not str
        or not reason
        or (source is not None and (type(source) is not str or not source))
        or (spec.exclude_source and source is None)
    ):
        raise SemanticChoiceError("Counter-set intent identity is malformed")
    return {
        "actor": actor,
        "spec": spec.to_dict(),
        "counter_name": counter_name,
        "amount": amount,
        _REASON_FIELD: reason,
        "source_ref": source,
    }


def _validate_counter_target_set_intent_identity(
    value: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise SemanticChoiceError(
            "Counter-target intent identity must be an object"
        )
    actual = set(value)
    if actual != _COUNTER_TARGET_SET_INTENT_FIELDS:
        missing = sorted(_COUNTER_TARGET_SET_INTENT_FIELDS - actual)
        unknown = sorted(actual - _COUNTER_TARGET_SET_INTENT_FIELDS)
        details = [
            *(f"missing {name}" for name in missing),
            *(f"unknown {name}" for name in unknown),
        ]
        raise SemanticChoiceError(
            "Counter-target intent identity fields: " + "; ".join(details)
        )
    actor = value["actor"]
    refs = value["object_refs"]
    maximum = value["maximum_targets"]
    counter_name = value["counter_name"]
    amount = value["amount"]
    reason = value[_REASON_FIELD]
    source = value["source_ref"]
    if (
        type(actor) is not str
        or not actor
        or not isinstance(refs, (list, tuple))
        or any(type(ref) is not str or not ref for ref in refs)
        or len(refs) != len(set(refs))
        or type(maximum) is not int
        or maximum <= 0
        or len(refs) > maximum
        or type(counter_name) is not str
        or not counter_name
        or type(amount) is not int
        or amount <= 0
        or type(reason) is not str
        or not reason
        or (source is not None and (type(source) is not str or not source))
    ):
        raise SemanticChoiceError(
            "Counter-target intent identity is malformed"
        )
    return {
        "actor": actor,
        "object_refs": list(refs),
        "maximum_targets": maximum,
        "counter_name": counter_name,
        "amount": amount,
        _REASON_FIELD: reason,
        "source_ref": source,
    }


def _validate_surveil_intent_identity(
    value: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != _SURVEIL_FIELDS:
        raise SemanticChoiceError(
            "Surveil intent identity fields are malformed"
        )
    raw_arrangement = value["arrangement"]
    if (
        not isinstance(raw_arrangement, Mapping)
        or set(raw_arrangement) != _SURVEIL_ARRANGEMENT_FIELDS
        or not isinstance(raw_arrangement["looked"], (list, tuple))
    ):
        raise SemanticChoiceError(
            "Surveil intent arrangement is malformed"
        )
    try:
        arrangement = SurveilArrangement(
            looked=tuple(
                SurveilObjectIdentity.from_dict(identity)
                for identity in raw_arrangement["looked"]
            ),
            top_top_first=tuple(raw_arrangement["top_top_first"]),
            graveyard_refs=tuple(raw_arrangement["graveyard_refs"]),
        )
        intent = SurveilLibraryIntent(
            actor=value["actor"],
            player=value["player"],
            arrangement=arrangement,
            requested_count=value["requested_count"],
            reason=value[_REASON_FIELD],
        )
    except (SurveilError, TypeError, ValueError) as exc:
        raise SemanticChoiceError(
            "Surveil intent identity is malformed"
        ) from exc
    return semantic_intent_identity(intent)[1]


def _validate_library_selection_intent_identity(
    value: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != (
        _LIBRARY_SELECTION_FIELDS
    ):
        raise SemanticChoiceError(
            "Library selection intent identity fields are malformed"
        )
    raw = value["arrangement"]
    if (
        not isinstance(raw, Mapping)
        or set(raw) != _LIBRARY_SELECTION_ARRANGEMENT_FIELDS
        or not isinstance(raw["looked"], (list, tuple))
    ):
        raise SemanticChoiceError(
            "Library selection intent arrangement is malformed"
        )
    try:
        arrangement = LibrarySelectionArrangement(
            looked=tuple(
                LibrarySelectionObjectIdentity.from_dict(identity)
                for identity in raw["looked"]
            ),
            selected_refs=tuple(raw["selected_refs"]),
            remainder_refs=tuple(raw["remainder_refs"]),
            remainder_destination=raw["remainder_destination"],
            remainder_order=raw["remainder_order"],
        )
        intent = LibrarySelectionIntent(
            actor=value["actor"],
            player=value["player"],
            arrangement=arrangement,
            reason=value[_REASON_FIELD],
            source_stack_ref=value["source_stack_ref"],
            looked_are_public=value["looked_are_public"],
            selected_are_public=value["selected_are_public"],
        )
    except (LibrarySelectionError, TypeError, ValueError) as exc:
        raise SemanticChoiceError(
            "Library selection intent identity is malformed"
        ) from exc
    return semantic_intent_identity(intent)[1]


def validate_semantic_intent_identity(
    kind: str,
    value: Mapping[str, Any],
) -> dict[str, Any]:
    if kind == "life_change":
        return _validate_life_change_intent_identity(value)
    if kind == "place_counters":
        return validate_counter_intent_identity(value)
    if kind == "place_counter_batch":
        return _validate_counter_batch_intent_identity(value)
    if kind == "place_counters_on_set":
        return _validate_counter_set_intent_identity(value)
    if kind == "place_counters_on_targets":
        return _validate_counter_target_set_intent_identity(value)
    if kind == "place_player_counters":
        return _validate_player_counter_intent_identity(value)
    if kind == "proliferate":
        return _validate_proliferate_intent_identity(value)
    if kind == "create_token":
        return _validate_create_token_intent_identity(value)
    if kind == "surveil_library":
        return _validate_surveil_intent_identity(value)
    if kind == "library_selection":
        return _validate_library_selection_intent_identity(value)
    if kind == "move_objects_simultaneously":
        return _validate_simultaneous_move_intent_identity(value)
    if kind != "zone_move":
        raise SemanticChoiceError("Unknown semantic intent continuation kind")
    if not isinstance(value, Mapping):
        raise SemanticChoiceError("Zone-move intent identity must be an object")
    actual = set(value)
    if (
        actual != _ZONE_MOVE_FIELDS
        and actual != _ZONE_MOVE_EFFECT_ENTRY_FIELDS
    ):
        expected = (
            _ZONE_MOVE_EFFECT_ENTRY_FIELDS
            if actual.intersection(
                {"expected_zone_change_counter", "effect_entry_counters"}
            )
            else _ZONE_MOVE_FIELDS
        )
        missing = sorted(expected - actual)
        unknown = sorted(actual - expected)
        details = [
            *(f"missing {name}" for name in missing),
            *(f"unknown {name}" for name in unknown),
        ]
        raise SemanticChoiceError(
            "Zone-move intent identity fields: " + "; ".join(details)
        )
    actor = value["actor"]
    object_ref = value["object_ref"]
    destination = value["destination"]
    reason = value[_REASON_FIELD]
    new_controller = value["new_controller"]
    tapped_policy = value["tapped_policy"]
    if any(
        type(item) is not str or not item
        for item in (actor, object_ref, destination, reason)
    ):
        raise SemanticChoiceError("Zone-move intent identity is malformed")
    if new_controller is not None and (
        type(new_controller) is not str or not new_controller
    ):
        raise SemanticChoiceError("Zone-move controller identity is malformed")
    if tapped_policy not in {"preserve", "land_entry", "tapped", "untapped"}:
        raise SemanticChoiceError("Zone-move tapped policy is malformed")
    for field_name in (
        "owned_only",
        "controlled_only",
        "semantic_events",
        "optional_if_missing",
    ):
        if type(value[field_name]) is not bool:
            raise SemanticChoiceError(
                f"Zone-move {field_name} must be a boolean"
            )
    result = {
        "actor": actor,
        "object_ref": object_ref,
        "expected_zones": _string_sequence(
            value["expected_zones"], field_name="expected_zones"
        ),
        "destination": destination,
        _REASON_FIELD: reason,
        "required_types": _string_sequence(
            value["required_types"], field_name="required_types"
        ),
        "owned_only": value["owned_only"],
        "controlled_only": value["controlled_only"],
        "new_controller": new_controller,
        "tapped_policy": tapped_policy,
        "semantic_events": value["semantic_events"],
        "optional_if_missing": value["optional_if_missing"],
    }
    if actual == _ZONE_MOVE_EFFECT_ENTRY_FIELDS:
        incarnation = value["expected_zone_change_counter"]
        if incarnation is not None and (
            type(incarnation) is not int or incarnation < 0
        ):
            raise SemanticChoiceError(
                "Zone-move zone-change counter is malformed"
            )
        raw_counters = value["effect_entry_counters"]
        if not isinstance(raw_counters, (list, tuple)):
            raise SemanticChoiceError(
                "Zone-move effect entry counters must be an array"
            )
        try:
            counters = tuple(
                EffectEntryCounter.from_dict(counter)
                for counter in raw_counters
            )
        except (EntryCounterError, TypeError) as exc:
            raise SemanticChoiceError(
                "Zone-move effect entry counters are malformed"
            ) from exc
        if counters and incarnation is None:
            raise SemanticChoiceError(
                "Effect-generated entry counters require pinned object identity"
            )
        if counters and value["destination"] != "battlefield":
            raise SemanticChoiceError(
                "Effect-generated entry counters require a battlefield move"
            )
        result.update(
            {
                "expected_zone_change_counter": incarnation,
                "effect_entry_counters": [
                    counter.to_dict() for counter in counters
                ],
            }
        )
    return result


def _validate_simultaneous_move_intent_identity(
    value: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != _SIMULTANEOUS_MOVE_FIELDS:
        raise SemanticChoiceError(
            "Simultaneous-move intent identity fields are malformed"
        )
    try:
        intent = MoveObjectsSimultaneouslyIntent(
            actor=value["actor"],
            object_refs=tuple(value["object_refs"]),
            expected_zones=tuple(value["expected_zones"]),
            destination=value["destination"],
            reason=value[_REASON_FIELD],
            owned_only=value["owned_only"],
            controlled_only=value["controlled_only"],
        )
    except (TypeError, ValueError) as exc:
        raise SemanticChoiceError(
            "Simultaneous-move intent identity is malformed"
        ) from exc
    return semantic_intent_identity(intent)[1]


def _validate_life_change_intent_identity(
    value: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != _LIFE_CHANGE_FIELDS:
        raise SemanticChoiceError(
            "Life-change intent identity fields are malformed"
        )
    actor = value["actor"]
    player = value["player"]
    amount = value["amount"]
    reason = value[_REASON_FIELD]
    source = value["source_ref"]
    if (
        any(
            type(item) is not str or not item
            for item in (actor, player, reason)
        )
        or type(amount) is not int
        or (source is not None and (type(source) is not str or not source))
    ):
        raise SemanticChoiceError("Life-change intent identity is malformed")
    return {
        "actor": actor,
        "player": player,
        "amount": amount,
        _REASON_FIELD: reason,
        "source_ref": source,
    }


def _validate_counter_batch_intent_identity(
    value: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != _COUNTER_BATCH_INTENT_FIELDS:
        raise SemanticChoiceError(
            "Counter batch intent identity fields are malformed"
        )
    raw_placements = value["placements"]
    if not isinstance(raw_placements, (list, tuple)):
        raise SemanticChoiceError(
            "Counter batch intent placements must be an array"
        )
    try:
        placements = tuple(
            CounterPlacementAmount(
                counter_name=raw["counter"],
                amount=raw["amount"],
            )
            for raw in raw_placements
            if isinstance(raw, Mapping)
            and set(raw) == _COUNTER_BATCH_ENTRY_FIELDS
        )
        if len(placements) != len(raw_placements):
            raise ValueError("Malformed counter batch entry")
        intent = PlaceCounterBatchIntent(
            actor=value["actor"],
            object_ref=value["object_ref"],
            placements=placements,
            reason=value[_REASON_FIELD],
            source_ref=value["source_ref"],
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise SemanticChoiceError(
            "Counter batch intent identity is malformed"
        ) from exc
    return {
        "actor": intent.actor,
        "object_ref": intent.object_ref,
        "placements": [
            {"counter": row.counter_name, "amount": row.amount}
            for row in intent.placements
        ],
        _REASON_FIELD: intent.reason,
        "source_ref": intent.source_ref,
    }


def _validate_proliferate_intent_identity(
    value: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise SemanticChoiceError(
            "Proliferate intent identity must be an object"
        )
    actual = set(value)
    if actual != _PROLIFERATE_FIELDS:
        missing = sorted(_PROLIFERATE_FIELDS - actual)
        unknown = sorted(actual - _PROLIFERATE_FIELDS)
        details = [
            *(f"missing {name}" for name in missing),
            *(f"unknown {name}" for name in unknown),
        ]
        raise SemanticChoiceError(
            "Proliferate intent identity fields: " + "; ".join(details)
        )
    actor = value["actor"]
    reason = value[_REASON_FIELD]
    source = value["source_ref"]
    raw_subjects = value["subjects"]
    if (
        type(actor) is not str
        or not actor
        or type(reason) is not str
        or not reason
        or (source is not None and (type(source) is not str or not source))
        or not isinstance(raw_subjects, (list, tuple))
    ):
        raise SemanticChoiceError("Proliferate intent identity is malformed")
    subjects: list[dict[str, Any]] = []
    identities: set[tuple[str, str]] = set()
    for raw in raw_subjects:
        if not isinstance(raw, Mapping) or set(raw) != _PROLIFERATE_SUBJECT_FIELDS:
            raise SemanticChoiceError(
                "Proliferate subject identity fields are malformed"
            )
        raw_names = raw["counter_names"]
        if not isinstance(raw_names, (list, tuple)):
            raise SemanticChoiceError(
                "Proliferate counter identity must be a sequence"
            )
        try:
            subject = ProliferateSubject(
                subject_kind=raw["subject_kind"],
                subject_id=raw["subject_id"],
                ref=raw["ref"],
                counter_names=tuple(raw_names),
                logical_object_id=raw["logical_object_id"],
            )
        except (TypeError, ValueError) as exc:
            raise SemanticChoiceError(
                "Proliferate subject identity is malformed"
            ) from exc
        identity = (subject.subject_kind, subject.subject_id)
        if identity in identities:
            raise SemanticChoiceError(
                "Proliferate subject identities must be unique"
            )
        identities.add(identity)
        subjects.append(
            {
                "subject_kind": subject.subject_kind,
                "subject_id": subject.subject_id,
                "ref": subject.ref,
                "counter_names": list(subject.counter_names),
                "logical_object_id": subject.logical_object_id,
            }
        )
    return {
        "actor": actor,
        "subjects": subjects,
        _REASON_FIELD: reason,
        "source_ref": source,
    }


def _validate_create_token_intent_identity(
    value: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != _CREATE_TOKEN_FIELDS:
        raise SemanticChoiceError(
            "Token-creation intent identity fields are malformed"
        )
    characteristics = value["characteristics"]
    temporary_keywords = value["temporary_keywords"]
    if not isinstance(characteristics, Mapping) or not isinstance(
        temporary_keywords, (list, tuple)
    ):
        raise SemanticChoiceError(
            "Token-creation intent identity is malformed"
        )
    try:
        intent = CreateTokenIntent(
            actor=value["actor"],
            controller=value["controller"],
            name=value["name"],
            quantity=value["quantity"],
            reason=value[_REASON_FIELD],
            characteristics=FrozenMap(characteristics),
            copy_of=value["copy_of"],
            temporary_keywords=tuple(temporary_keywords),
            sacrifice_at_end_step=value["sacrifice_at_end_step"],
            sacrifice_on_controller_end_step=(
                value["sacrifice_on_controller_end_step"]
            ),
        )
    except (TypeError, ValueError) as exc:
        raise SemanticChoiceError(
            "Token-creation intent identity is malformed"
        ) from exc
    return {
        "actor": intent.actor,
        "controller": intent.controller,
        "name": intent.name,
        "quantity": intent.quantity,
        _REASON_FIELD: intent.reason,
        "characteristics": thaw_value(intent.characteristics),
        "copy_of": intent.copy_of,
        "temporary_keywords": list(intent.temporary_keywords),
        "sacrifice_at_end_step": intent.sacrifice_at_end_step,
        "sacrifice_on_controller_end_step": (
            intent.sacrifice_on_controller_end_step
        ),
    }


def with_replacement_selections(
    intent: Any,
    selections: Sequence[str | FrozenMap | Mapping[str, Any]],
) -> (
    PlaceCountersIntent
    | LifeChangeIntent
    | PlaceCounterBatchIntent
    | PlaceCountersOnSetIntent
    | PlaceCountersOnTargetsIntent
    | PlacePlayerCountersIntent
    | ProliferateIntent
    | CreateTokenIntent
    | SurveilLibraryIntent
    | LibrarySelectionIntent
    | ZoneMoveIntent
    | MoveObjectsSimultaneouslyIntent
):
    if not isinstance(
        intent,
        (
            PlaceCountersIntent,
            LifeChangeIntent,
            PlaceCounterBatchIntent,
            PlaceCountersOnSetIntent,
            PlaceCountersOnTargetsIntent,
            PlacePlayerCountersIntent,
            ProliferateIntent,
            CreateTokenIntent,
            SurveilLibraryIntent,
            LibrarySelectionIntent,
            ZoneMoveIntent,
            MoveObjectsSimultaneouslyIntent,
        ),
    ):
        raise SemanticChoiceError(
            "Semantic replacement continuation no longer names a supported intent"
        )
    return replace(intent, replacement_selections=tuple(selections))


def serialized_replacement_selections(
    selections: Sequence[str | FrozenMap | Mapping[str, Any]],
) -> list[str | dict[str, Any]]:
    return [
        value if isinstance(value, str) else thaw_value(value)
        for value in selections
    ]
