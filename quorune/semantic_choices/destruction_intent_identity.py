from __future__ import annotations

"""Closed continuation identities for replacement-aware destruction intents."""

from typing import Any, Mapping

from ..affected_permanents import AffectedPermanentSetSpec
from ..semantic_runtime.intents import DestroyPermanentIntent, DestroyPermanentSetIntent, DestroyPermanentTargetsIntent
from .model import SemanticChoiceError


DESTRUCTION_INTENT_TYPES = (DestroyPermanentIntent, DestroyPermanentSetIntent, DestroyPermanentTargetsIntent)


def destruction_intent_identity(intent: Any) -> tuple[str, dict[str, Any]]:
    common = {"actor": intent.actor, "reason": intent.reason,
              "regeneration_prohibited": intent.regeneration_prohibited}
    if isinstance(intent, DestroyPermanentIntent):
        return "destroy_permanent", {**common, "object_ref": intent.object_ref}
    if isinstance(intent, DestroyPermanentTargetsIntent):
        return "destroy_permanent_targets", {**common, "object_refs": list(intent.object_refs)}
    if isinstance(intent, DestroyPermanentSetIntent):
        return "destroy_permanent_set", {**common, "spec": intent.spec.to_dict(), "source_ref": intent.source_ref}
    raise SemanticChoiceError("Destruction continuation intent is not typed")


def validate_destruction_intent_identity(kind: str, value: Mapping[str, Any]) -> dict[str, Any]:
    common = {"actor", "reason", "regeneration_prohibited"}
    fields = {"destroy_permanent": common | {"object_ref"},
              "destroy_permanent_targets": common | {"object_refs"},
              "destroy_permanent_set": common | {"spec", "source_ref"}}
    if kind not in fields or not isinstance(value, Mapping) or set(value) != fields[kind]:
        raise SemanticChoiceError("Destruction continuation fields are malformed")
    if any(type(value[field]) is not str or not value[field] for field in ("actor", "reason")):
        raise SemanticChoiceError("Destruction continuation actor or reason is malformed")
    try:
        if kind == "destroy_permanent":
            if type(value["object_ref"]) is not str or not value["object_ref"]:
                raise ValueError("Destruction object reference is malformed")
            intent = DestroyPermanentIntent(**dict(value))
        elif kind == "destroy_permanent_targets":
            intent = DestroyPermanentTargetsIntent(**dict(value))
        else:
            intent = DestroyPermanentSetIntent(**{**value, "spec": AffectedPermanentSetSpec.from_dict(value["spec"])})
    except (TypeError, ValueError) as exc:
        raise SemanticChoiceError(str(exc)) from exc
    return destruction_intent_identity(intent)[1]
