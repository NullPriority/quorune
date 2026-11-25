from __future__ import annotations

"""Closed capability shapes for destruction and regeneration effects."""

from typing import Any, Iterable, Mapping, Sequence

from ..affected_permanents import (
    AffectedPermanentSetError,
    AffectedPermanentSetSpec,
    KICKED_ENTRY_DESTRUCTION_CAPABILITIES,
    KICKED_ENTRY_DESTRUCTION_FIELD,
    KickedEntryDestructionSpec,
    PermanentControllerRelation as AffectedControllerRelation,
)
from ..attachment_references import (
    AttachmentReferenceError,
    AttachmentReferenceSpec,
)
from ..compiler.fixed_source_effect_sequences import SOURCE_ZONE_OBJECT
from .permanent_predicate_capability_shapes import (
    direct_permanent_target_schema_is_closed,
    direct_target_predicate_capabilities,
)


def regeneration_covered_mechanics(supplied: set[str]) -> set[str]:
    """Project the regeneration owners back to their existing mechanic names."""
    mapping = {
        'regenerate': {'permanent.regeneration.fixed_effect', 'permanent.regeneration.self_activation'},
        'attached-destruction-set': {'permanent.destroy.attached_set'},
        'regeneration-prohibition': {'permanent.destroy.regeneration_prohibition'},
    }
    return {mechanic for mechanic, capabilities in mapping.items() if supplied.intersection(capabilities)}


def targeted_destruction_node_capabilities(
    *,
    effects: Sequence[Mapping[str, Any]],
    target_schema: Mapping[str, Any] | None,
    mechanic_ids: Iterable[str],
) -> tuple[str, ...]:
    """Return capabilities only for the closed direct destruction grammar."""

    from .aura_attachment_targets import aura_attachment_destruction_dependencies

    mechanics = {str(value).casefold() for value in mechanic_ids}
    try:
        attached = aura_attachment_destruction_dependencies(effects, target_schema)
    except ValueError:
        return ()
    if attached:
        return attached if {"destroy", "cr-115-targets"}.issubset(mechanics) and "regeneration-prohibition" not in mechanics else ()
    if target_schema is None and len(effects)==1 and set(effects[0])=={'op','card'} and effects[0].get('op')=='destroy' and 'destroy' in mechanics and 'cr-115-targets' not in mechanics and isinstance(effects[0].get('card'),Mapping):
        try:AttachmentReferenceSpec.from_dict(effects[0]['card'])
        except (TypeError,ValueError):return ()
        return ('permanent.destroy.effect','attachment.reference.current_or_lki')
    if (target_schema is None and len(effects)==1 and effects[0].get('op')=='destroy' and effects[0].get('card')=='$event.permanent_object'
            and 'destroy' in mechanics and 'cr-115-targets' not in mechanics):
        prohibited=effects[0].get('regeneration_prohibited') is True
        if set(effects[0])!={'op','card',*({'regeneration_prohibited'} if prohibited else set())} or ('regeneration-prohibition' in mechanics) is not prohibited:return ()
        return ('permanent.destroy.effect','resolution.event_object.fixed_result',*(('permanent.destroy.regeneration_prohibition',) if prohibited else ()))
    if (
        not {"destroy", "cr-115-targets"}.issubset(mechanics)
        or len(effects) != 1
        or not direct_permanent_target_schema_is_closed(target_schema)
    ):
        return ()
    effect = effects[0]
    regeneration_prohibited = effect.get("regeneration_prohibited")
    expected_fields = {"op", "card"} | (
        {"regeneration_prohibited"}
        if regeneration_prohibited is True
        else set()
    )
    if (
        set(effect) != expected_fields
        or effect.get("op") != "destroy"
        or effect.get("card") != "$target.0"
        or ("regeneration-prohibition" in mechanics)
        is not (regeneration_prohibited is True)
    ):
        return ()
    assert target_schema is not None
    return (
        "permanent.destroy.effect",
        *(
            ("permanent.destroy.regeneration_prohibition",)
            if regeneration_prohibited is True
            else ()
        ),
        *direct_target_predicate_capabilities(target_schema),
        "target.revalidate_resolution",
    )


def self_regeneration_node_capabilities(
    *,
    effects: Sequence[Mapping[str, Any]],
    target_schema: Mapping[str, Any] | None,
    mechanic_ids: Iterable[str],
) -> tuple[str, ...]:
    """Return ownership for one closed fixed-object regeneration action."""

    mechanics = {str(value).casefold() for value in mechanic_ids}
    contextual_mechanics = {
        value
        for value in mechanics
        if value.startswith("trigger-") or value.startswith("cr-603-")
        or value == "fixed-typed-event-effect-trigger"
    }
    effect_mechanics = mechanics - contextual_mechanics
    if len(effects) != 1:
        return ()
    effect = effects[0]
    if set(effect) != {"op", "card"} or effect.get("op") != "regenerate":
        return ()
    reference = effect.get("card")
    if target_schema is None and effect_mechanics == {"regenerate"}:
        if reference == SOURCE_ZONE_OBJECT:
            return ("permanent.regeneration.self_activation",)
        if not isinstance(reference, Mapping):
            return ()
        try:
            AttachmentReferenceSpec.from_dict(reference)
        except (AttachmentReferenceError, TypeError):
            return ()
        return (
            "permanent.regeneration.fixed_effect",
            "permanent.regeneration.self_activation",
            "attachment.reference.current_or_lki",
        )
    if (
        effect_mechanics != {"regenerate", "cr-115-targets"}
        or reference != "$target.0"
        or not direct_permanent_target_schema_is_closed(target_schema)
    ):
        return ()
    assert target_schema is not None
    return (
        "permanent.regeneration.fixed_effect",
        "permanent.regeneration.self_activation",
        *direct_target_predicate_capabilities(target_schema),
        "target.revalidate_resolution",
    )


def kicked_entry_destruction_runtime_dependencies(program) -> tuple[str, ...]:
    from .effect_shape_walkers import nested_effects

    values = (*program.effects, *getattr(program, "handlers", ()))
    if not any(KICKED_ENTRY_DESTRUCTION_FIELD in effect for effect in nested_effects(values)):
        return ()
    if ((program.event, program.active_zone) != ("permanent.enter.self", "battlefield")
            or program.event_condition is not None or program.target_schema is not None
            or len(program.effects) != 1 or getattr(program, "handlers", ())):
        raise ValueError("Kicked entry destruction requires one complete unconditional self-entry trigger")
    KickedEntryDestructionSpec.from_effect(program.effects[0])
    return KICKED_ENTRY_DESTRUCTION_CAPABILITIES


def _relative_power_destruction_capabilities(effect, target_schema, mechanics):
    from .qualified_destruction import (
        TARGET_RELATIVE_POWER_DESTRUCTION_DEPENDENCIES, relative_power_destruction_instruction,
    )
    from ..replacement.immutable import thaw_value

    if "cr-115-targets" not in mechanics:
        return ()
    try:
        spec = relative_power_destruction_instruction(effect)
    except (TypeError, ValueError):
        return ()
    target = thaw_value(target_schema)
    if target != spec.target_schema() or type(target["count"]) is not int:
        return ()
    return TARGET_RELATIVE_POWER_DESTRUCTION_DEPENDENCIES


def mass_destruction_node_capabilities(
    *,
    effects: Sequence[Mapping[str, Any]],
    target_schema: Mapping[str, Any] | None,
    mechanic_ids: Iterable[str],
) -> tuple[str, ...]:
    """Return capabilities only for the closed fixed-set destruction grammar."""

    mechanics = {str(value).casefold() for value in mechanic_ids}
    if (
        not {"destroy", "destroy-fixed-set"}.issubset(mechanics)
        or len(effects) != 1
    ):
        return ()
    effect = effects[0]
    if KICKED_ENTRY_DESTRUCTION_FIELD in effect:
        if target_schema is not None or "fixed-kicked-entry-trigger" not in mechanics:
            return ()
        try:
            KickedEntryDestructionSpec.from_effect(effect)
        except (ValueError, TypeError):
            return ()
        return KICKED_ENTRY_DESTRUCTION_CAPABILITIES
    from .qualified_destruction import (
        SOURCE_INCARNATION_EXCLUSION_FIELD, is_relative_power_destruction_set, source_incarnation_excluding_destruction,
    )
    if is_relative_power_destruction_set(effect.get("set")):
        return _relative_power_destruction_capabilities(effect, target_schema, mechanics)
    if SOURCE_INCARNATION_EXCLUSION_FIELD in effect:
        if target_schema is not None or "cr-115-targets" in mechanics:
            return ()
        try:
            source_incarnation_excluding_destruction(effect)
        except (TypeError, ValueError):
            return ()
        return ("permanent.destroy.fixed_set",)

    from .destruction_result_model import (
        CREATURE_TYPE_DESTRUCTION_DEPENDENCIES, CREATURE_TYPE_DESTRUCTION_FIELD,
        ChosenCreatureTypeDestruction, DESTRUCTION_RESULT_MECHANIC, DestructionFollowup,
    )

    if CREATURE_TYPE_DESTRUCTION_FIELD in effect:
        if target_schema is not None or mechanics.intersection({"cr-115-targets", "regeneration-prohibition"}):
            return ()
        try:
            ChosenCreatureTypeDestruction.from_effect(effect)
        except (TypeError, ValueError):
            return ()
        return CREATURE_TYPE_DESTRUCTION_DEPENDENCIES

    has_followup = "followup" in effect
    if has_followup != (DESTRUCTION_RESULT_MECHANIC in mechanics):
        return ()
    try:
        result_caps = (
            DestructionFollowup.from_destruction_effect(effect).capability_dependencies if has_followup else ()
        )
    except (TypeError, ValueError):
        return ()
    regeneration_prohibited = effect.get("regeneration_prohibited")
    expected_fields = {"op", "source", "set"} | ({"followup"} if has_followup else set()) | (
        {"regeneration_prohibited"}
        if regeneration_prohibited is True
        else set()
    )
    if (
        set(effect) != expected_fields
        or effect.get("op") != "destroy_all"
        or effect.get("source") != "$source"
        or ("regeneration-prohibition" in mechanics)
        is not (regeneration_prohibited is True)
    ):
        return ()
    try:
        spec = AffectedPermanentSetSpec.from_dict(effect["set"])
    except (AffectedPermanentSetError, KeyError, TypeError):
        return ()
    targeted = spec.controller_relation is AffectedControllerRelation.TARGET_PLAYER
    from ..attachment_destruction_model import AttachmentDestructionSetSpec, ATTACHED_DESTRUCTION_CAPABILITY, AttachedDestructionScope
    if isinstance(spec, AttachmentDestructionSetSpec):
        if has_followup or regeneration_prohibited is not None or 'attached-destruction-set' not in mechanics:
            return ()
        expected = None
        if spec.targeted:
            kind = 'land' if spec.scope is AttachedDestructionScope.AURAS_ON_LAND else 'creature'
            expected = {'zones':['battlefield'],'categories':['permanent'],'count':1,'types_any':[kind]}
        if target_schema != expected or ('cr-115-targets' in mechanics) is not spec.targeted:
            return ()
        if spec.targeted and spec.target_ref != '$target.0':
            return ()
        return ('permanent.destroy.fixed_set', ATTACHED_DESTRUCTION_CAPABILITY,
                *(('target.revalidate_resolution',) if spec.targeted else ()))
    schema = dict(target_schema or {})
    valid_player_schemas = (
        {
            "zones": ["player"],
            "categories": ["player"],
            "count": 1,
            "player_relation": "any",
        },
        {
            "zones": ["player"],
            "categories": ["player"],
            "count": 1,
            "player_relation": "opponent",
        },
    )
    prohibition_capability = (
        ("permanent.destroy.regeneration_prohibition",)
        if regeneration_prohibited is True
        else ()
    )
    from .qualified_destruction import qualified_destruction_capabilities
    query_capabilities=qualified_destruction_capabilities(spec.query)
    if spec.mana_value_filter is not None:
        query_capabilities=(*query_capabilities,*spec.mana_value_filter.capability_dependencies)
    if targeted:
        if "cr-115-targets" not in mechanics or schema not in valid_player_schemas:
            return ()
        if spec.target_controller != "$target.0":
            return ()
        return (
            "permanent.destroy.fixed_set",
            *query_capabilities,
            *prohibition_capability,
            *result_caps,
            "target.revalidate_resolution",
        )
    if target_schema is not None or "cr-115-targets" in mechanics:
        return ()
    return ("permanent.destroy.fixed_set", *query_capabilities, *prohibition_capability, *result_caps)


__all__ = [
    "mass_destruction_node_capabilities",
    "self_regeneration_node_capabilities",
    "targeted_destruction_node_capabilities",
]
