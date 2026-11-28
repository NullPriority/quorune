"""The closed Surveil identity used by replacement continuations."""

from __future__ import annotations

from typing import Any, Mapping

from ..rules.library_surveillance import SurveilArrangement, SurveilError, SurveilObjectIdentity
from ..semantic_runtime.intents import SurveilLibraryIntent
from .model import SemanticChoiceError


_REASON_FIELD = "reason"
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


def surveil_intent_identity(
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


def validate_surveil_intent_identity(
    value: Mapping[str, Any], intent_identity,
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
    return intent_identity(intent)[1]
