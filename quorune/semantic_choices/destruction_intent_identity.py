from __future__ import annotations

"""Closed continuation identities for replacement-aware destruction intents."""

from typing import Any, Mapping

from ..affected_permanents import AffectedPermanentSetSpec
from ..rules.nonbasic_land_source_untap import decode_single_destruction_followup
from ..rules.controlled_destruction_source_counters import decode_announced_destruction_followup
from ..semantic_runtime.intents import DestroyPermanentIntent, DestroyPermanentSetIntent, DestroyPermanentTargetsIntent
from .model import SemanticChoiceError


DESTRUCTION_INTENT_TYPES = (DestroyPermanentIntent, DestroyPermanentSetIntent, DestroyPermanentTargetsIntent)


def destruction_intent_identity(intent: Any) -> tuple[str, dict[str, Any]]:
    common = {"actor": intent.actor, "reason": intent.reason,
              "regeneration_prohibited": intent.regeneration_prohibited}
    if isinstance(intent, (DestroyPermanentIntent, DestroyPermanentTargetsIntent)) and intent.followup is not None:
        common["followup"] = intent.followup.to_dict()
    if isinstance(intent, DestroyPermanentIntent):
        return "destroy_permanent", {**common, "object_ref": intent.object_ref}
    if isinstance(intent, DestroyPermanentTargetsIntent):
        return "destroy_permanent_targets", {**common, "object_refs": list(intent.object_refs)}
    if isinstance(intent, DestroyPermanentSetIntent):
        if intent.ordered_exclusion is not None:
            common["ordered_exclusion"] = intent.ordered_exclusion.to_dict()
            if intent.followup is not None and intent.followup.uses_owned_return:
                common["followup"] = intent.followup.to_dict()
        return "destroy_permanent_set", {**common, "spec": intent.spec.to_dict(), "source_ref": intent.source_ref}
    raise SemanticChoiceError("Destruction continuation intent is not typed")


def validate_destruction_intent_identity(kind: str, value: Mapping[str, Any]) -> dict[str, Any]:
    common = {"actor", "reason", "regeneration_prohibited"}
    fields = {"destroy_permanent": common | {"object_ref"},
              "destroy_permanent_targets": common | {"object_refs"},
              "destroy_permanent_set": common | {"spec", "source_ref"}}
    if kind not in fields or not isinstance(value, Mapping):
        raise SemanticChoiceError("Destruction continuation fields are malformed")
    followup = "followup" in value
    ordered = kind == "destroy_permanent_set" and "ordered_exclusion" in value
    expected = fields[kind] | ({"followup"} if followup else set()) | ({"ordered_exclusion"} if ordered else set())
    if set(value) != expected:
        raise SemanticChoiceError("Destruction continuation fields are malformed")
    if any(type(value[field]) is not str or not value[field] for field in ("actor", "reason")):
        raise SemanticChoiceError("Destruction continuation actor or reason is malformed")
    try:
        if kind == "destroy_permanent":
            if type(value["object_ref"]) is not str or not value["object_ref"]:
                raise ValueError("Destruction object reference is malformed")
            data = dict(value)
            if "followup" in data:
                data["followup"] = decode_single_destruction_followup(data["followup"])
            intent = DestroyPermanentIntent(**data)
        elif kind == "destroy_permanent_targets":
            data = dict(value)
            if "followup" in data:
                data["followup"] = decode_announced_destruction_followup(data["followup"], resolved=True)
            intent = DestroyPermanentTargetsIntent(**data)
        else:
            from ..rules.qualified_destruction import RelativePowerDestructionSetSpec, is_relative_power_destruction_set

            raw = value["spec"]
            spec = (RelativePowerDestructionSetSpec.from_dict(raw) if is_relative_power_destruction_set(raw)
                    else AffectedPermanentSetSpec.from_dict(raw))
            if isinstance(spec, RelativePowerDestructionSetSpec) and spec.target_ref.startswith("$"):
                raise ValueError("Relative-power destruction continuation requires its resolved target")
            from ..ordered_incarnation_exclusion import OrderedIncarnationExclusion

            data = {**value, "spec": spec}
            if ordered:
                data["ordered_exclusion"] = OrderedIncarnationExclusion.from_dict(data["ordered_exclusion"])
            if followup:
                from ..rules.destruction_result_model import DestructionFollowup

                data["followup"] = DestructionFollowup.from_dict(data["followup"])
                if not ordered or not data["followup"].uses_owned_return:
                    raise ValueError("Only the ordered owned-return result extends this continuation")
            intent = DestroyPermanentSetIntent(**data)
    except (TypeError, ValueError) as exc:
        raise SemanticChoiceError(str(exc)) from exc
    return destruction_intent_identity(intent)[1]
