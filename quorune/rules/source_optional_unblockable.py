"""A source and an optional targeted creature use one ordinary unblockable rule."""

from collections.abc import Mapping

from ..continuous_effect_state import ResolutionEffectSource
from ..replacement.immutable import thaw_value
from .effect_shape_walkers import nested_effects
from .linked_clue_copy import linked_clue_sacrifice_condition
from .temporary_declaration_restrictions import temporary_declaration_restriction


SOURCE_OPTIONAL_UNBLOCKABLE_KIND = "source_optional_creature_unblockable"
SOURCE_OPTIONAL_UNBLOCKABLE_CAPABILITY = "continuous.resolution.source_optional_unblockable"
SOURCE_OPTIONAL_UNBLOCKABLE_OPERATION = "grant_declaration_restriction_until_end_of_turn"


def source_optional_unblockable_target_schema(*, controlled=False):
    schema = {
        "zones": ["battlefield"], "categories": ["permanent"], "types_any": ["creature"],
        "up_to": 1, "source_exclusion": True,
    }
    if controlled:
        schema["controller_relation"] = "you"
    return schema


def source_optional_unblockable_effect():
    return {
        "op": SOURCE_OPTIONAL_UNBLOCKABLE_OPERATION, "cards": "$targets",
        "restriction": {"kind": SOURCE_OPTIONAL_UNBLOCKABLE_KIND, "schema_version": 1,
                        "source": "$source.zone_object"},
    }


def source_optional_unblockable_mechanics():
    return ("cr-115-targets", "cr-611-continuous-effects",
            *temporary_declaration_restriction("unblockable").mechanics)


def is_source_optional_unblockable(effect):
    spec = effect.get("restriction")
    return isinstance(spec, Mapping) and spec.get("kind") == SOURCE_OPTIONAL_UNBLOCKABLE_KIND


def source_optional_unblockable_node_capabilities(*, effects, target_schema, mechanic_ids):
    if len(effects) != 1 or thaw_value(effects[0]) != source_optional_unblockable_effect():
        return ()
    if type(effects[0]["restriction"]["schema_version"]) is not int:
        return ()
    schema = thaw_value(target_schema)
    if (schema not in (source_optional_unblockable_target_schema(),
                       source_optional_unblockable_target_schema(controlled=True))
            or type(schema["up_to"]) is not int or schema["source_exclusion"] is not True
            or not set(source_optional_unblockable_mechanics()) <= set(mechanic_ids)):
        return ()
    return (
        SOURCE_OPTIONAL_UNBLOCKABLE_CAPABILITY, "combat.declaration.typed_components",
        "continuous.resolution.declaration_rules_until_end_of_turn", "target.revalidate_resolution",
    )


def source_optional_unblockable_runtime_dependencies(program):
    from ..reflexive_trigger_model import SOURCE_COUNTER_REFLEXIVE_FIELD

    # The reflexive parent independently validates the complete stored child.
    if any(SOURCE_COUNTER_REFLEXIVE_FIELD in effect for effect in program.effects):
        return ()
    if not any(is_source_optional_unblockable(effect) for effect in nested_effects(
            (*program.effects, *getattr(program, "handlers", ())))):
        return ()
    required = source_optional_unblockable_node_capabilities(
        effects=program.effects, target_schema=program.target_schema, mechanic_ids=program.coverage,
    )
    if (not required or program.event != "permanent.sacrificed" or program.active_zone != "battlefield"
            or thaw_value(program.event_condition) != linked_clue_sacrifice_condition()
            or program.cost_schema is not None or getattr(program, "handlers", ())):
        raise ValueError("Source and optional creature require the complete controller Clue sacrifice trigger")
    return (*required, "trigger.event.normalized_zone_change", "trigger.placement.apnap")


def prepare_source_optional_unblockable(host, effect):
    fields = {"op", "cards", "restriction"}
    spec = effect["restriction"]
    if (set(effect) - {"reason", "_runtime_source"} != fields
            or effect["op"] != SOURCE_OPTIONAL_UNBLOCKABLE_OPERATION
            or set(spec) != {"kind", "schema_version", "source"}
            or type(spec["schema_version"]) is not int or spec["schema_version"] != 1):
        raise ValueError("The source and optional creature effect has closed fields")
    source = ResolutionEffectSource.from_effect(effect)
    if not source.object_id or not source.logical_object_id:
        raise ValueError("The source and optional creature require the original resolving source")
    current = host.state.cards.get(source.object_id)
    live = current.ref if (current is not None and current.zone == "battlefield" and not current.phased_out
                            and current.logical_object_id == source.logical_object_id) else None
    if spec["source"] != live:
        raise ValueError("The effect changed its original source incarnation")
    refs = effect["cards"]
    if (not isinstance(refs, list) or len(refs) > 1
            or any(type(ref) is not str or not ref or ref == live for ref in refs)):
        raise ValueError("The effect needs at most one surviving other creature target")
    return {**effect, "cards": ([live] if live is not None else []) + refs, "restriction": "unblockable"}
