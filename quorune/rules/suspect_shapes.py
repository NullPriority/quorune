"""Close Suspect independently for every announced or source-bound operand."""

from collections.abc import Mapping

from ..attachment_references import AttachmentReferenceKind, AttachmentReferenceSpec
from ..suspect_model import SUSPECT_CAPABILITY, SUSPECT_MECHANIC, SUSPECT_OPERATION
from .permanent_predicate_capability_shapes import (
    direct_permanent_target_schema_is_closed, direct_target_predicate_capabilities,
)

_ENCHANTED = AttachmentReferenceSpec(AttachmentReferenceKind.ENCHANTED, "creature").to_dict()
_TARGET_REFS = ("$target.0", "$target_group.target_0.0", "$target_group.target_1.0")
_TARGET_LISTS = ("$targets", *(f"$target_group.clause_{index}" for index in range(4)))


def suspect_descriptor_dependencies(effect):
    if not isinstance(effect, Mapping) or effect.get("op") != SUSPECT_OPERATION:
        return ()
    if dict(effect) == {"op": SUSPECT_OPERATION, "clear": "suspected_creatures"}:
        return (SUSPECT_CAPABILITY,)
    if set(effect) == {"op", "card"}:
        ref = effect["card"]
        if ref == "$source.zone_object" or ref == _ENCHANTED:
            return (SUSPECT_CAPABILITY,)
        if ref in _TARGET_REFS:
            return (SUSPECT_CAPABILITY, "target.revalidate_resolution")
    if set(effect) == {"op", "cards"} and effect["cards"] in _TARGET_LISTS:
        return (SUSPECT_CAPABILITY, "target.revalidate_resolution")
    raise ValueError("Suspect requires a represented creature target, source or attached reference")


def suspect_node_capabilities(*, effects, target_schema, mechanic_ids):
    if SUSPECT_MECHANIC not in mechanic_ids or len(effects) != 1:
        return ()
    effect = effects[0]
    try:
        required = suspect_descriptor_dependencies(effect)
    except (TypeError, ValueError):
        return ()
    if not required:
        return ()
    optional_targets = effect.get("cards") == "$targets"
    if effect.get("card") != "$target.0" and not optional_targets:
        return required if target_schema is None and "target.revalidate_resolution" not in required else ()
    if "cr-115-targets" not in mechanic_ids or not isinstance(target_schema, Mapping):
        return ()
    schema = dict(target_schema)
    if optional_targets and "up_to" not in schema:
        return ()
    if "up_to" in schema:
        optional = schema.pop("up_to")
        if type(optional) is not int or optional != 1 or "count" in schema:
            return ()
        schema["count"] = 1
    if not direct_permanent_target_schema_is_closed(schema) or (
        schema.get("types_any") != ["creature"] and "creature" not in schema.get("types_all", ())
    ):
        return ()
    return (*required, *direct_target_predicate_capabilities(schema))


def is_closed_suspect_program(program):
    required = suspect_node_capabilities(
        effects=program.effects, target_schema=program.target_schema, mechanic_ids=program.coverage,
    )
    return bool(required) and set(required).issubset(program.capability_dependencies)
