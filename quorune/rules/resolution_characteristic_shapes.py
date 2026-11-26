from __future__ import annotations

"""Strict v2 selection/result capability binding beside the existing owner."""

from typing import Any, Mapping

from ..attachment_references import ATTACHMENT_INFORMATION_CAPABILITY, AttachmentReferenceSpec
from ..object_predicate import ObjectQuerySpec, object_query_capabilities
from ..next_turn_effect import next_turn_effect_dependencies
from ..source_tap_effect import source_tap_effect_dependencies
from ..basic_type_setting_model import BASIC_TYPE_SETTING_CAPABILITY
from ..target_land_type_model import (
    FIXED_TARGET_LAND_TYPE_CAPABILITY, FIXED_TARGET_LAND_TYPE_MECHANIC, FixedTargetLandTypeSpec,
    SOURCE_PRESENT_LAND_TYPE_CAPABILITY, NEXT_UNTAP_LAND_TYPE_CAPABILITY, SOURCE_EXIT_LAND_TYPE_CAPABILITY,
)
from ..resolution_characteristic_model import (
    CHARACTERISTIC_OPERATION, FIXED_TARGET_COLOR_CAPABILITY, FIXED_TARGET_COLOR_MECHANIC,
    FIXED_TARGET_COLOR_VERSION, decode_fixed_target_color, fixed_target_color_schema,
    DERIVED_CHARACTERISTIC_CAPABILITY, RECIPIENT_MANA_VALUE, DerivedResolutionCharacteristicsSpec,
    IndefiniteSourceAnimationSpec, INDEFINITE_SOURCE_ANIMATION_CAPABILITY, INDEFINITE_SOURCE_ANIMATION_MECHANIC,
    IndefiniteEnchantmentFormSpec, INDEFINITE_ENCHANTMENT_FORM_CAPABILITY, INDEFINITE_ENCHANTMENT_FORM_MECHANIC,
    PERMANENT_CHARACTERISTIC_CAPABILITY, PERMANENT_CHARACTERISTIC_MECHANIC, FixedResolutionSubtypeGrantSpec,
    FIXED_SUBTYPE_BASE_CAPABILITY,
    FixedResolutionModifierSpec, FixedResolutionSubtypeSettingSpec, decode_temporary_creature_type_choice,
    FixedResolutionCardTypeAdditionSpec, TARGET_CARD_TYPE_ADDITION_CAPABILITY, TARGET_CARD_TYPE_ADDITION_MECHANIC,
    IndefiniteTargetLandAnimationSpec, INDEFINITE_TARGET_LAND_ANIMATION_CAPABILITY, INDEFINITE_TARGET_LAND_ANIMATION_MECHANIC,
    fixed_resolution_characteristic_instruction, TargetPlayerCreatureOverwriteSpec,
    target_player_creature_overwrite_schema, TargetCreatureAbilityLossSpec,
    TARGET_CREATURE_ABILITY_LOSS_VERSION, target_creature_ability_loss_schema,
)
from ..compiler.fixed_resolution_characteristic_queries import fixed_resolution_characteristic_query_is_closed
from .creature_subtype_set_results import (
    CREATURE_SUBTYPE_SET_RESULT_CAPABILITY, CREATURE_SUBTYPE_SET_RESULT_MECHANIC,
    PAID_CONTROLLER_SUBTYPE_SET_CAPABILITY,
    creature_subtype_set_node_capabilities, is_creature_subtype_set_result,
)


_CONTINUOUS_EFFECT_MECHANIC_OWNERS = frozenset({
    "continuous.power_toughness.fixed_anthem",
    "continuous.resolution.fixed_characteristics_until_end_of_turn",
    "continuous.resolution.declaration_rules_until_end_of_turn",
    "combat.declaration.source_block_pair",
    "continuous.resolution.fixed_keyword_zone_object",
    INDEFINITE_SOURCE_ANIMATION_CAPABILITY,
    INDEFINITE_ENCHANTMENT_FORM_CAPABILITY,
    FIXED_TARGET_COLOR_CAPABILITY,
    TARGET_CARD_TYPE_ADDITION_CAPABILITY,
    INDEFINITE_TARGET_LAND_ANIMATION_CAPABILITY,
})


def continuous_resolution_covered_mechanics(supplied: set[str]) -> set[str]:
    covered = {"cr-611-continuous-effects"} if supplied.intersection(_CONTINUOUS_EFFECT_MECHANIC_OWNERS) else set()
    if INDEFINITE_SOURCE_ANIMATION_CAPABILITY in supplied:
        covered.add(INDEFINITE_SOURCE_ANIMATION_MECHANIC)
    if INDEFINITE_ENCHANTMENT_FORM_CAPABILITY in supplied:
        covered.add(INDEFINITE_ENCHANTMENT_FORM_MECHANIC)
    if TARGET_CARD_TYPE_ADDITION_CAPABILITY in supplied:
        covered.add(TARGET_CARD_TYPE_ADDITION_MECHANIC)
    if INDEFINITE_TARGET_LAND_ANIMATION_CAPABILITY in supplied:
        covered.add(INDEFINITE_TARGET_LAND_ANIMATION_MECHANIC)
    if {CREATURE_SUBTYPE_SET_RESULT_CAPABILITY, PAID_CONTROLLER_SUBTYPE_SET_CAPABILITY} & supplied:
        covered.update((CREATURE_SUBTYPE_SET_RESULT_MECHANIC, "cr-611-continuous-effects"))
    if FIXED_TARGET_LAND_TYPE_CAPABILITY in supplied:
        covered.update((FIXED_TARGET_LAND_TYPE_MECHANIC, "cr-611-continuous-effects"))
    return covered


def fixed_target_color_node_capabilities(*, effects, target_schema, mechanic_ids):
    from ..resolution_color_model import TARGET_COLOR_VERSIONS
    if any(e.get('schema_version') in TARGET_COLOR_VERSIONS for e in effects):
        from .resolution_colors import resolution_color_node_capabilities
        return resolution_color_node_capabilities(effects=effects,target_schema=target_schema,mechanic_ids=mechanic_ids)
    if (len(effects) != 1 or target_schema != fixed_target_color_schema()
            or not {FIXED_TARGET_COLOR_MECHANIC, "cr-611-continuous-effects", "cr-115-targets"} <= set(mechanic_ids)):
        return ()
    try:
        decode_fixed_target_color(effects[0])
    except (TypeError, ValueError):
        return ()
    return (FIXED_TARGET_COLOR_CAPABILITY, "target.revalidate_resolution")


def fixed_target_color_runtime_dependencies(program):
    from .resolution_colors import resolution_color_runtime_dependencies
    required = set(resolution_color_runtime_dependencies(program))

    def visit(value):
        if isinstance(value, Mapping):
            if value.get("op") == CHARACTERISTIC_OPERATION and value.get("schema_version") == FIXED_TARGET_COLOR_VERSION:
                owners = fixed_target_color_node_capabilities(effects=(value,), target_schema=program.target_schema,
                    mechanic_ids=(FIXED_TARGET_COLOR_MECHANIC, "cr-611-continuous-effects", "cr-115-targets"))
                if not owners:
                    raise ValueError("Fixed target color lacks complete target and layer authority")
                required.update(owners)
                return
            for child in value.values():
                visit(child)
        elif isinstance(value, (list, tuple)):
            for child in value:
                visit(child)

    visit(program.effects)
    return tuple(sorted(required))


def fixed_resolution_characteristic_set_is_closed(query: ObjectQuerySpec) -> bool:
    from dataclasses import replace
    symbolic=replace(query,controller='$controller' if query.controller is not None else None,
                     excluded_controllers=('$controller',) if query.excluded_controllers else ())
    return not query.keywords_none and fixed_resolution_characteristic_query_is_closed(symbolic,target_schema=None)


def temporary_creature_type_node_capabilities(*, effects, target_schema, mechanic_ids):
    if len(effects) != 1 or "cr-611-continuous-effects" not in mechanic_ids:
        return ()
    try:
        selection, _excluded = decode_temporary_creature_type_choice(effects[0])
    except (TypeError, ValueError, KeyError):
        return ()
    required = {PERMANENT_CHARACTERISTIC_CAPABILITY, "continuous.resolution.fixed_characteristics_until_end_of_turn"}
    if selection == "$target.0":
        from .permanent_predicate_capability_shapes import direct_permanent_target_schema_is_closed, direct_target_predicate_capabilities

        if ("cr-115-targets" not in mechanic_ids or not direct_permanent_target_schema_is_closed(target_schema)
                or (target_schema.get("types_any") != ["creature"] and "creature" not in target_schema.get("types_all", ()))):
            return ()
        required.update(("target.revalidate_resolution", *direct_target_predicate_capabilities(target_schema)))
    elif target_schema is not None:
        return ()
    elif isinstance(selection, ObjectQuerySpec):
        required.update(object_query_capabilities(selection))
    elif isinstance(selection, AttachmentReferenceSpec):
        required.add(ATTACHMENT_INFORMATION_CAPABILITY)
    return tuple(sorted(required))


def temporary_creature_type_runtime_dependencies(program):
    effects = program.effects
    chosen = [effect for effect in effects if effect.get("op") == "choose_creature_type" and "schema_version" in effect]
    if not chosen:
        return ()
    required = temporary_creature_type_node_capabilities(
        effects=effects, target_schema=program.target_schema,
        mechanic_ids=("cr-611-continuous-effects", "cr-115-targets"),
    )
    if not required:
        raise ValueError("A temporary creature type choice requires its complete subject and layer owners")
    return required


def is_target_creature_ability_loss(effect):
    return (isinstance(effect, Mapping) and effect.get("op") == CHARACTERISTIC_OPERATION
            and effect.get("schema_version") == TARGET_CREATURE_ABILITY_LOSS_VERSION)


def targeted_tap_then_ability_loss_capabilities(*, effects, target_schema, mechanic_ids):
    from .tap_state_capability_shapes import targeted_tap_state_node_capabilities

    if (len(effects) != 2 or effects[0] != {"op": "tap", "card": "$target.0"}
            or target_schema != target_creature_ability_loss_schema()
            or not {"cr-611-continuous-effects", PERMANENT_CHARACTERISTIC_MECHANIC} <= set(mechanic_ids)):
        return ()
    try:
        spec, card = fixed_resolution_characteristic_instruction(effects[1])
    except (ValueError, TypeError, KeyError):
        return ()
    if not isinstance(spec, TargetCreatureAbilityLossSpec) or card != "$target.0":
        return ()
    tap = targeted_tap_state_node_capabilities(
        effects=(effects[0],), target_schema=target_schema, mechanic_ids=mechanic_ids,
    )
    return (*tap, PERMANENT_CHARACTERISTIC_CAPABILITY, "continuous.resolution.fixed_characteristics_until_end_of_turn") if tap else ()


def fixed_resolution_characteristic_capabilities(*, effects: Any, target_schema: Any, mechanic_ids: Any) -> tuple[str, ...]:
    mechanics=set(mechanic_ids)
    if any(is_target_creature_ability_loss(effect) for effect in effects):
        return targeted_tap_then_ability_loss_capabilities(
            effects=effects, target_schema=target_schema, mechanic_ids=mechanics,
        )
    from .source_block_defender import is_source_block_defender_instruction, source_block_defender_node_capabilities

    if any(is_source_block_defender_instruction(effect) for effect in effects):
        return source_block_defender_node_capabilities(effects=effects, target_schema=target_schema, mechanics=mechanics)
    if any(is_creature_subtype_set_result(effect) for effect in effects):
        return creature_subtype_set_node_capabilities(effects=effects, target_schema=target_schema, mechanics=mechanics)
    if FIXED_TARGET_LAND_TYPE_MECHANIC in mechanics:
        from ..replacement.immutable import FrozenMap

        if (len(effects) != 1 or not {"cr-611-continuous-effects", "cr-115-targets"} <= mechanics
                or not isinstance(target_schema, Mapping) or type(target_schema.get("count")) is not int):
            return ()
        try:
            spec, selection = fixed_resolution_characteristic_instruction(effects[0])
        except (TypeError, ValueError, KeyError):
            return ()
        if (not isinstance(spec, FixedTargetLandTypeSpec) or selection != "$target.0"
                or FrozenMap(spec.target_schema()) != FrozenMap(target_schema)):
            return ()
        required = (FIXED_TARGET_LAND_TYPE_CAPABILITY, BASIC_TYPE_SETTING_CAPABILITY,
                    "continuous.resolution.fixed_characteristics_until_end_of_turn", "target.revalidate_resolution")
        if spec.duration == "until_source_leaves":
            return (*required, SOURCE_PRESENT_LAND_TYPE_CAPABILITY)
        if spec.duration == "until_recipient_controller_untap":
            return (*required, NEXT_UNTAP_LAND_TYPE_CAPABILITY)
        if spec.duration == "until_source_battlefield_exit":
            return (*required, SOURCE_EXIT_LAND_TYPE_CAPABILITY)
        return required
    if ('cr-611-continuous-effects' not in mechanics or len(effects) != 1
            or not mechanics.intersection({PERMANENT_CHARACTERISTIC_MECHANIC,
                INDEFINITE_SOURCE_ANIMATION_MECHANIC, INDEFINITE_ENCHANTMENT_FORM_MECHANIC,
                TARGET_CARD_TYPE_ADDITION_MECHANIC,INDEFINITE_TARGET_LAND_ANIMATION_MECHANIC})):
        return ()
    try:spec,selection=fixed_resolution_characteristic_instruction(effects[0])
    except (TypeError,ValueError,KeyError):return ()
    if isinstance(spec,IndefiniteTargetLandAnimationSpec):
        expected={'zones':['battlefield'],'categories':['permanent'],'count':1,
                  'types_any':['land'],'controller_relation':'you'}
        return spec.capability_dependencies if (selection=='$target.0' and target_schema==expected
            and {INDEFINITE_TARGET_LAND_ANIMATION_MECHANIC,'cr-115-targets'}<=mechanics) else ()
    if isinstance(spec, FixedResolutionCardTypeAdditionSpec):
        from .permanent_predicate_capability_shapes import direct_permanent_target_schema_is_closed, direct_target_predicate_capabilities

        if (not {TARGET_CARD_TYPE_ADDITION_MECHANIC, "cr-115-targets"} <= mechanics
                or selection != "$target.0" or not direct_permanent_target_schema_is_closed(target_schema)):
            return ()
        if ((spec.power or spec.toughness) and target_schema.get("types_any") != ["creature"]
                and "creature" not in target_schema.get("types_all", ())):
            return ()
        return (*spec.capability_dependencies, *direct_target_predicate_capabilities(target_schema))
    if isinstance(spec, IndefiniteEnchantmentFormSpec):
        return spec.capability_dependencies if (
            target_schema is None and INDEFINITE_ENCHANTMENT_FORM_MECHANIC in mechanics
        ) else ()
    if isinstance(spec, IndefiniteSourceAnimationSpec):
        return spec.capability_dependencies if (
            target_schema is None and INDEFINITE_SOURCE_ANIMATION_MECHANIC in mechanics
        ) else ()
    if PERMANENT_CHARACTERISTIC_MECHANIC not in mechanics or isinstance(spec, FixedResolutionSubtypeSettingSpec):
        return ()
    dependencies={PERMANENT_CHARACTERISTIC_CAPABILITY,'continuous.resolution.fixed_characteristics_until_end_of_turn',
                  *next_turn_effect_dependencies(effects[0]), *source_tap_effect_dependencies(effects[0])}
    if isinstance(spec, TargetPlayerCreatureOverwriteSpec):
        if target_schema != target_player_creature_overwrite_schema() or "cr-115-targets" not in mechanics:
            return ()
        return tuple(sorted({*dependencies, "target.revalidate_resolution"}))
    if isinstance(spec, DerivedResolutionCharacteristicsSpec):
        from ..compiler.direct_target import DirectPermanentTargetSpec
        from ..query_effect_amount_model import DECLARED_AMOUNT_CAPABILITY

        if selection != "$target.0" or "cr-115-targets" not in mechanics:
            return ()
        if spec.quantity == RECIPIENT_MANA_VALUE:
            expected = DirectPermanentTargetSpec(types_any=("artifact",), types_none=("creature",))
        else:
            expected = DirectPermanentTargetSpec(types_any=("creature",))
            dependencies.add(DECLARED_AMOUNT_CAPABILITY)
        if target_schema != expected.to_target_schema():
            return ()
        return tuple(sorted({*dependencies, DERIVED_CHARACTERISTIC_CAPABILITY, "target.revalidate_resolution"}))
    if isinstance(spec, FixedResolutionModifierSpec):
        all_creatures = ObjectQuerySpec(zones=("battlefield",), types_all=("creature",))
        return tuple(sorted(dependencies)) if target_schema is None and selection == all_creatures else ()
    if not {keyword.casefold() for keyword in spec.keywords}<=mechanics:return ()
    if isinstance(spec, FixedResolutionSubtypeGrantSpec):
        if spec.base_power_toughness is not None:
            if target_schema is not None or not isinstance(selection, ObjectQuerySpec):
                return ()
            return tuple(sorted({*dependencies, FIXED_SUBTYPE_BASE_CAPABILITY, *object_query_capabilities(selection)}))
        from .permanent_predicate_capability_shapes import (
            direct_permanent_target_schema_is_closed, direct_target_predicate_capabilities,
        )

        if selection != "$target.0" or not direct_permanent_target_schema_is_closed(target_schema):
            return ()
        if (
            "cr-115-targets" not in mechanics
            or (target_schema.get("types_any") != ["creature"] and "creature" not in target_schema.get("types_all", ()))
        ):
            return ()
        return tuple(sorted({*dependencies, "target.revalidate_resolution", *direct_target_predicate_capabilities(target_schema)}))
    if isinstance(selection,ObjectQuerySpec):
        if selection.keywords_none or not fixed_resolution_characteristic_query_is_closed(selection,target_schema=target_schema):return ()
        if spec.card_types is None and 'creature' not in selection.types_all:return ()
    elif selection=='$source.zone_object':
        if target_schema is not None:return ()
    elif selection=='$target.0':
        from .node_capability_shapes import direct_target_predicate_capabilities
        if not isinstance(target_schema,Mapping) or target_schema.get('count')!=1 or target_schema.get('zones')!=['battlefield'] or target_schema.get('categories')!=['permanent']:return ()
        if spec.card_types is None and target_schema.get('types_any')!=['creature']:return ()
        dependencies.update(direct_target_predicate_capabilities(target_schema))
    else:return ()
    if target_schema is not None:
        if 'cr-115-targets' not in mechanics:return ()
        dependencies.add('target.revalidate_resolution')
    return tuple(sorted(dependencies))


def validate_derived_characteristic_program(program) -> tuple[str, ...]:
    owners = fixed_resolution_characteristic_capabilities(
        effects=program.effects, target_schema=program.target_schema, mechanic_ids=program.coverage,
    )
    if DERIVED_CHARACTERISTIC_CAPABILITY not in owners:
        raise ValueError("Derived base stats require a complete animation and its exact target schema")
    spec, _selection = fixed_resolution_characteristic_instruction(program.effects[0])
    if spec.quantity != RECIPIENT_MANA_VALUE and (program.event != "resolve" or not program.ability_id.startswith("spell:")):
        raise ValueError("Announced X for base stats requires its spell's cast")
    return owners
