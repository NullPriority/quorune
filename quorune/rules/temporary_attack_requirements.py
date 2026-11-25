"""Minimum ownership and live projection of resolved attack requirements."""
from collections.abc import Mapping

from ..attachment_references import ATTACHMENT_INFORMATION_CAPABILITY, AttachmentReferenceKind, AttachmentReferenceSpec
from ..replacement.immutable import thaw_value
from ..temporary_attack_requirement_model import (
    TEMPORARY_ATTACK_REQUIREMENT_KIND, TEMPORARY_ATTACK_REQUIREMENT_CAPABILITY,
    TemporaryAttackRequirementSpec,
)


_MINIMUM = (TEMPORARY_ATTACK_REQUIREMENT_CAPABILITY, "combat.declaration.typed_components",
            "continuous.resolution.declaration_rules_until_end_of_turn")


def _requirement_dependencies(effect, target_schema):
    if set(effect) != {"op", "card", "restriction"} or effect["op"] != "grant_declaration_restriction_until_end_of_turn":
        raise ValueError("Resolved attack requirements have one closed subject instruction")
    spec = TemporaryAttackRequirementSpec.from_dict(effect["restriction"])
    if thaw_value(target_schema) != spec.target_schema():
        raise ValueError("Resolved attack requirement target schema changed")
    if spec.schema_version == 3:
        reference = AttachmentReferenceSpec(AttachmentReferenceKind.ENCHANTED, "creature", schema_version=2)
        if AttachmentReferenceSpec.from_dict(effect["card"]) != reference:
            raise ValueError("An enchanted attack requirement needs the current or last-known source attachment")
        return (*_MINIMUM, ATTACHMENT_INFORMATION_CAPABILITY)
    if effect["card"] != "$target.0":
        raise ValueError("A targeted attack requirement must keep its original creature target")
    return (*_MINIMUM, "target.revalidate_resolution")


def temporary_attack_requirement_node_capabilities(*, effects, target_schema, mechanic_ids):
    mechanics = {"cr-611-continuous-effects", "cr-508-declare-attackers-step"}
    if target_schema is not None:
        mechanics.add("cr-115-targets")
    if len(effects) != 1 or not mechanics <= set(mechanic_ids):
        return ()
    try:
        return _requirement_dependencies(effects[0], target_schema)
    except (TypeError, ValueError):
        return ()


def temporary_attack_requirement_runtime_dependencies(program):
    required = set()

    def visit(value):
        if isinstance(value, Mapping):
            spec = value.get("restriction")
            if (value.get("op") == "grant_declaration_restriction_until_end_of_turn"
                    and isinstance(spec, Mapping) and spec.get("kind") == TEMPORARY_ATTACK_REQUIREMENT_KIND):
                if spec.get("schema_version") in {3, 4} and (
                        (program.event, program.active_zone) != ("activate", "battlefield")
                        or tuple(program.effects) != (value,)):
                    raise ValueError("A source-bound attack requirement needs its original activated ability")
                required.update(_requirement_dependencies(value, program.target_schema))
            for child in value.values():
                visit(child)
        elif isinstance(value, (tuple, list)):
            for child in value:
                visit(child)

    visit((program.effects, program.handlers))
    return tuple(sorted(required))
