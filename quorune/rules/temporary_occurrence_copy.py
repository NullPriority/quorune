"""Optional temporary copying of the original other controlled entrant."""

from collections.abc import Mapping

from ..defined_token_faces import single_face_copy_values
from ..errors import GameRuleError
from ..replacement.immutable import FrozenMap, thaw_value
from ..token_copy_recipes import TokenCopyRecipeSpec
from ..token_copy_references import copy_reference_snapshot
from .temporary_copy_subject import _copy_object_from_reference, copy_subject_object_reference
from .temporary_self_copy import TEMPORARY_SELF_COPY_OPERATION


TEMPORARY_OCCURRENCE_COPY_OPERATION = "offer_temporary_occurrence_copy"
TEMPORARY_OCCURRENCE_COPY_CAPABILITY = "continuous.resolution.temporary_occurrence_creature_copy"
TEMPORARY_OCCURRENCE_COPY_MECHANIC = "temporary-occurrence-creature-copy"
TEMPORARY_OCCURRENCE_COPY_COMMIT_VERSION = "temporary_occurrence_copy_commit_version"
TEMPORARY_OCCURRENCE_COPY_RULES = ("113.7a", "400.7", "514.2", "603.2", "608.2d", "608.2h", "707.2", "702.26b")
_EVENT_MODEL = TokenCopyRecipeSpec("event_object")
_EVENT_CONDITION = {
    "all": [
        {"field": "controller", "op": "eq", "value": "$source.controller"},
        {"field": "card", "op": "ne", "value": "$source.ref"},
    ]
}
_DEPENDENCIES = (
    TEMPORARY_OCCURRENCE_COPY_CAPABILITY, "effect.choice.optional_fixed",
    "trigger.event.normalized_zone_change",
)


def temporary_occurrence_copy_effect():
    return {
        "op": TEMPORARY_OCCURRENCE_COPY_OPERATION, "schema_version": 1,
        "player": "$controller", "receiver": "$source.zone_object", "copy_spec": _EVENT_MODEL.to_dict(),
    }


def temporary_occurrence_copy_condition_matches(condition):
    return thaw_value(condition) == _EVENT_CONDITION


def represents_temporary_occurrence_copy(effects):
    return any(effect.get("op") == TEMPORARY_OCCURRENCE_COPY_OPERATION for effect in effects)


def _compiled_copy_is_closed(effect):
    return (isinstance(effect, Mapping) and type(effect.get("schema_version")) is int
            and thaw_value(effect) == temporary_occurrence_copy_effect())


def temporary_occurrence_copy_node_capabilities(*, effects, target_schema, mechanic_ids):
    if len(effects) != 1 or target_schema is not None or not _compiled_copy_is_closed(effects[0]):
        return ()
    return _DEPENDENCIES


def temporary_occurrence_copy_runtime_dependencies(program):
    if not represents_temporary_occurrence_copy(program.effects):
        return ()
    required = temporary_occurrence_copy_node_capabilities(
        effects=program.effects, target_schema=program.target_schema, mechanic_ids=program.coverage,
    )
    if (not required or program.event != "creature.enter" or program.active_zone != "battlefield"
            or not temporary_occurrence_copy_condition_matches(program.event_condition)):
        raise ValueError("Temporary occurrence copying requires its original other controlled creature entry")
    return required


def bind_temporary_occurrence_copy(host, item, raw_effect, effect):
    if not _compiled_copy_is_closed(raw_effect) or item.kind != "triggered_ability" or effect.get("player") != item.controller:
        raise ValueError("Temporary occurrence copy lost its original trigger and controller")
    receiver_ref = effect["receiver"]
    if receiver_ref is None:
        return None
    receiver = host.state.cards.get(item.source_object_id)
    if (receiver is None or receiver.ref != receiver_ref or receiver.zone != "battlefield"
            or receiver.phased_out or receiver.logical_object_id != item.context.get("source_logical_object_id")):
        return None
    model = copy_reference_snapshot(host, item, _EVENT_MODEL, use_phased_lki=True)
    if not isinstance(model.get("snapshot"), Mapping) or not isinstance(model["snapshot"].get("characteristics"), Mapping):
        raise ValueError("Temporary occurrence copy has no retained copiable values")
    values = single_face_copy_values(model["snapshot"]["characteristics"])
    return {"op": TEMPORARY_OCCURRENCE_COPY_OPERATION, "schema_version": 1, "player": item.controller,
            "receiver": copy_subject_object_reference(receiver), "model_ref": model["ref"], "values": values}


def validate_bound_temporary_occurrence_copy(effect, actor):
    if (not isinstance(effect, Mapping)
            or set(effect) != {"op", "schema_version", "player", "receiver", "model_ref", "values"}
            or effect["op"] != TEMPORARY_OCCURRENCE_COPY_OPERATION or type(effect["schema_version"]) is not int
            or effect["schema_version"] != 1 or effect["player"] != actor
            or type(effect["model_ref"]) is not str or not effect["model_ref"]
            or not isinstance(effect["receiver"], Mapping) or not isinstance(effect["values"], Mapping)):
        raise ValueError("Temporary occurrence copy requires its sealed controller, receiver and copiable values")


def temporary_occurrence_copy_commit_effect(effect):
    return FrozenMap({"op": TEMPORARY_SELF_COPY_OPERATION, TEMPORARY_OCCURRENCE_COPY_COMMIT_VERSION: 1,
                      "receiver": effect["receiver"], "model_ref": effect["model_ref"], "values": effect["values"]})


def prepare_temporary_occurrence_copy_commit(host, effect):
    fields = {"op", TEMPORARY_OCCURRENCE_COPY_COMMIT_VERSION, "receiver", "model_ref", "values"}
    if (not fields <= set(effect) <= fields | {"reason", "_runtime_source"}
            or effect["op"] != TEMPORARY_SELF_COPY_OPERATION
            or type(effect[TEMPORARY_OCCURRENCE_COPY_COMMIT_VERSION]) is not int
            or effect[TEMPORARY_OCCURRENCE_COPY_COMMIT_VERSION] != 1
            or type(effect["model_ref"]) is not str or not effect["model_ref"]
            or not isinstance(effect["values"], Mapping)):
        raise GameRuleError("Temporary occurrence copy commit has an invalid retained frame")
    receiver = _copy_object_from_reference(host, effect["receiver"])
    return effect["model_ref"], (receiver,) if receiver is not None else (), thaw_value(effect["values"])
