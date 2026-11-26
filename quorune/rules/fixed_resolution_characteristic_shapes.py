from __future__ import annotations

"""Capability closure for fixed resolution-locked characteristic sets."""

from typing import Any, Iterable, Mapping, Sequence

from ..attachment_references import (
    ATTACHMENT_INFORMATION_CAPABILITY, AttachmentReferenceError, AttachmentReferenceKind, AttachmentReferenceSpec,
)
from ..compiler.fixed_resolution_characteristic_queries import (
    CONTROLLED_FLYING_CREATURES_QUERY, fixed_resolution_characteristic_query_is_closed,
)
from ..keyword_abilities import (
    FIXED_CHARACTERISTIC_KEYWORDS,
    FIXED_CHARACTERISTIC_KEYWORD_CAPABILITIES,
)
from ..object_predicate import ObjectQueryError, ObjectQuerySpec, object_query_capabilities
from ..replacement.immutable import thaw_value
from ..next_turn_effect import NEXT_TURN_EFFECT_CAPABILITY, split_next_turn_effect
from .same_name_creature_modifiers import (
    SAME_NAME_STAT_VERSION_FIELD, same_name_creature_stat_node_capabilities, same_name_creature_stat_runtime_dependencies,
)
from ..resolution_characteristic_model import (CHARACTERISTIC_OPERATION, TARGET_CARD_TYPE_ADDITION_VERSION,
    TARGET_CARD_TYPE_ADDITION_CAPABILITY, TARGET_CARD_TYPE_ADDITION_MECHANIC)


CONTROLLED_FLYING_STATS_CAPABILITY = "continuous.resolution.controlled_flying_fixed_stats"
FIXED_RESOLUTION_CHARACTERISTICS_CAPABILITY = (
    "continuous.resolution.fixed_characteristics_until_end_of_turn"
)


EQUIPPED_STAT_REFERENCE = AttachmentReferenceSpec(AttachmentReferenceKind.EQUIPPED, "permanent", schema_version=2)


def is_attached_characteristic_effect(effect) -> bool:
    reference = effect.get("card")
    if not isinstance(reference, Mapping) or reference.get("kind") != "source_attachment":
        return False
    op = effect.get("op")
    # Versioned characteristic results have their own independent shape owners.
    return op == "modify_stats_until_end_of_turn" or (
        op == "apply_source_characteristics_until_end_of_turn" and "schema_version" not in effect
    )


def is_equipped_stat_mode_effect(effect) -> bool:
    return (effect.get("op") == "modify_stats_until_end_of_turn"
            and is_attached_characteristic_effect(effect)
            and effect["card"].get("relation") == AttachmentReferenceKind.EQUIPPED.value)


def _closed_attached_stat_reference(value) -> bool:
    try:
        reference = AttachmentReferenceSpec.from_dict(value)
    except AttachmentReferenceError:
        return False
    return (reference.schema_version == 2 and reference.required_card_type == "permanent"
            and reference.relation in {AttachmentReferenceKind.ENCHANTED, AttachmentReferenceKind.EQUIPPED})


def fixed_attached_stat_node_capabilities(*, effects, target_schema, mechanic_ids) -> tuple[str, ...]:
    if target_schema is not None or len(effects) != 1 or "cr-611-continuous-effects" not in mechanic_ids:
        return ()
    effect = effects[0]
    if (not isinstance(effect, Mapping) or set(effect) != {"op", "card", "power", "toughness"}
            or effect.get("op") != "modify_stats_until_end_of_turn"
            or type(effect.get("power")) is not int or type(effect.get("toughness")) is not int
            or not (effect["power"] or effect["toughness"])):
        return ()
    if not _closed_attached_stat_reference(effect["card"]):
        return ()
    return (FIXED_RESOLUTION_CHARACTERISTICS_CAPABILITY, ATTACHMENT_INFORMATION_CAPABILITY)


def fixed_attached_characteristic_node_capabilities(*, effects, target_schema, mechanic_ids) -> tuple[str, ...]:
    if target_schema is not None or len(effects) != 1 or "cr-611-continuous-effects" not in mechanic_ids:
        return ()
    effect = effects[0]
    if not is_attached_characteristic_effect(effect):
        return ()
    if effect["op"] == "modify_stats_until_end_of_turn":
        return fixed_attached_stat_node_capabilities(effects=effects, target_schema=None, mechanic_ids=mechanic_ids)
    keywords = effect.get("keywords")
    if (not isinstance(keywords, (list, tuple)) or not 1 <= len(keywords) <= 2
            or any(type(keyword) is not str or keyword not in FIXED_CHARACTERISTIC_KEYWORDS for keyword in keywords)
            or len(set(keywords)) != len(keywords)):
        return ()
    try:
        reference = AttachmentReferenceSpec.from_dict(effect["card"])
    except AttachmentReferenceError:
        return ()
    if reference == AttachmentReferenceSpec(AttachmentReferenceKind.ENCHANTED, "permanent", schema_version=2):
        source_kind = "enchantment"
    elif (tuple(keywords) == ("Hexproof",)
          and reference == AttachmentReferenceSpec(AttachmentReferenceKind.EQUIPPED, "permanent", schema_version=2)):
        source_kind = "artifact"
    else:
        return ()
    from ..compiler.fixed_target_effect_sequences import FixedSourceCharacteristicsTemplate

    template = FixedSourceCharacteristicsTemplate(source_kind=source_kind, keywords=tuple(keywords))
    if thaw_value(effect) != {**template.effects[0], "card": thaw_value(effect["card"])}:
        return ()
    if type(effect["power"]) is not int or type(effect["toughness"]) is not int:
        return ()
    return tuple(sorted({FIXED_RESOLUTION_CHARACTERISTICS_CAPABILITY, ATTACHMENT_INFORMATION_CAPABILITY,
                         *(cap for keyword in keywords for cap in FIXED_CHARACTERISTIC_KEYWORD_CAPABILITIES[keyword])}))


EACH_COLOR_PROTECTION_FIELD = "protection_from_each_color"


def fixed_set_each_color_protection_is_closed(effect, query, *, controller):
    """Accept only a complete until-end-of-turn grant to the original controlled creature set."""
    return (
        set(effect) - {"_runtime_source"} == {
            "op", "predicate", "power", "toughness", "keywords", EACH_COLOR_PROTECTION_FIELD,
        }
        and effect[EACH_COLOR_PROTECTION_FIELD] is True
        and effect["op"] == "modify_all_matching_permanents_until_end_of_turn"
        and all(type(effect[key]) is int and effect[key] == 0 for key in ("power", "toughness"))
        and effect["predicate"] == query.to_dict()
        and query == ObjectQuerySpec(zones=("battlefield",), types_all=("creature",), controller=controller)
    )


def fixed_resolution_characteristic_set_node_capabilities(
    *,
    effects: Sequence[Mapping[str, Any]],
    target_schema: Mapping[str, Any] | None,
    mechanic_ids: Iterable[str],
) -> tuple[str, ...]:
    """Own one fixed or chosen resolution-locked public characteristic result."""

    from .target_player_stat_untap import TARGET_PLAYER_STAT_UNTAP_VERSION, target_player_stat_untap_node_capabilities

    if len(effects) == 1 and TARGET_PLAYER_STAT_UNTAP_VERSION in effects[0]:
        return target_player_stat_untap_node_capabilities(
            effects=effects, target_schema=target_schema, mechanic_ids=mechanic_ids,
        )
    if len(effects) == 1 and SAME_NAME_STAT_VERSION_FIELD in effects[0]:
        return same_name_creature_stat_node_capabilities(
            effects=effects, target_schema=target_schema, mechanic_ids=mechanic_ids,
        )
    if len(effects)==1 and isinstance(effects[0],Mapping) and 'event_player_set_version' in effects[0]:
        from .event_player_modifiers import event_player_modifier_node_capabilities
        return event_player_modifier_node_capabilities(effects=effects,target_schema=target_schema,mechanic_ids=mechanic_ids)

    if len(effects) == 1 and isinstance(effects[0], Mapping) and effects[0].get("op") == "choose_creature_type":
        from ..resolution_characteristic_model import (
            CHOSEN_CREATURE_TYPE_MODIFIER_FIELD, CHOSEN_CREATURE_TYPE_RESULT_FIELD,
            chosen_creature_type_result_dependencies,
        )

        if {CHOSEN_CREATURE_TYPE_RESULT_FIELD, CHOSEN_CREATURE_TYPE_MODIFIER_FIELD} & effects[0].keys():
            if target_schema is not None:
                return ()
            try:
                return chosen_creature_type_result_dependencies(effects[0])
            except (TypeError, ValueError):
                return ()
        from .resolution_characteristic_shapes import temporary_creature_type_node_capabilities

        return temporary_creature_type_node_capabilities(
            effects=effects, target_schema=target_schema, mechanic_ids=mechanic_ids,
        )

    mechanics = {str(value).casefold() for value in mechanic_ids}
    if (
        "cr-611-continuous-effects" not in mechanics
        or len(effects) != 1
    ):
        return ()
    try:
        effect, next_turn = split_next_turn_effect(effects[0])
    except ValueError:
        return ()
    has_keywords = "keywords" in effect
    expected_fields = {"op", "predicate", "power", "toughness"}
    if has_keywords:
        expected_fields.add("keywords")
    each_color_protection = EACH_COLOR_PROTECTION_FIELD in effect
    if each_color_protection:
        expected_fields.add(EACH_COLOR_PROTECTION_FIELD)
    if (
        set(effect) != expected_fields
        or effect.get("op")
        != "modify_all_matching_permanents_until_end_of_turn"
        or type(effect.get("power")) is not int
        or type(effect.get("toughness")) is not int
        or not isinstance(effect.get("predicate"), Mapping)
    ):
        return ()
    try:
        query = ObjectQuerySpec.from_dict(effect["predicate"])
    except (ObjectQueryError, TypeError):
        return ()
    if dict(effect["predicate"]) != query.to_dict():
        return ()
    if each_color_protection and (
        next_turn or target_schema is not None or "protection" not in mechanics
        or not fixed_set_each_color_protection_is_closed(effect, query, controller="$controller")
    ):
        return ()
    if query == CONTROLLED_FLYING_CREATURES_QUERY:
        if target_schema is not None or has_keywords or next_turn or not (effect["power"] or effect["toughness"]):
            return ()
        return (CONTROLLED_FLYING_STATS_CAPABILITY, FIXED_RESOLUTION_CHARACTERISTICS_CAPABILITY)
    if not fixed_resolution_characteristic_query_is_closed(query, target_schema=target_schema):
        return ()
    if has_keywords:
        raw_keywords = effect["keywords"]
        if (
            not isinstance(raw_keywords, list)
            or not raw_keywords
            or any(type(keyword) is not str for keyword in raw_keywords)
            or len(set(raw_keywords)) != len(raw_keywords)
            or any(
                keyword not in FIXED_CHARACTERISTIC_KEYWORDS
                for keyword in raw_keywords
            )
        ):
            return ()
        required_keyword_mechanics = {
            keyword.casefold() for keyword in raw_keywords
        }
        if (
            not required_keyword_mechanics.issubset(mechanics)
            or mechanics.intersection(
                {
                    "continuous.ability.fixed_query_keyword_grant",
                    "continuous.power_toughness.fixed_anthem",
                }
            )
        ):
            return ()
    dependencies = {FIXED_RESOLUTION_CHARACTERISTICS_CAPABILITY, *object_query_capabilities(query)}
    if each_color_protection:
        dependencies.add("protection.typed.debt")
    if next_turn:
        dependencies.add(NEXT_TURN_EFFECT_CAPABILITY)
    if query.state_predicate is not None:
        dependencies.add("state_query.permanent.public_state_predicate")
    if target_schema is not None:
        if "cr-115-targets" not in mechanics:
            return ()
        dependencies.add("target.revalidate_resolution")
    return tuple(sorted(dependencies))


def _runtime_stat_amount(value):
    from ..query_effect_amount_model import (
        CAST_X_AMOUNT_KIND, DECLARED_AMOUNT_CAPABILITY, PUBLIC_QUERY_AMOUNT_KIND,
        PUBLIC_QUERY_AMOUNT_CAPABILITY, CastXAmountSpec, PublicQueryAmountSpec,
    )
    from ..scalar_effect_amount_model import SCALAR_AMOUNT_KIND, SCALAR_AMOUNT_CAPABILITY, ScalarEffectAmountSpec

    if type(value) is int:
        return value, set()
    if not isinstance(value, Mapping):
        raise ValueError('Resolution-set stat amount requires an integer or typed quantity')
    kind = value.get('kind')
    if kind=='source_entry_x_amount':
        from ..entry_x_amount_model import EntryXAmountSpec,ENTRY_X_AMOUNT_CAPABILITY
        spec=EntryXAmountSpec.from_dict(value)
        required={ENTRY_X_AMOUNT_CAPABILITY,DECLARED_AMOUNT_CAPABILITY}
    elif kind == CAST_X_AMOUNT_KIND:
        spec = CastXAmountSpec.from_dict(value)
        required = {DECLARED_AMOUNT_CAPABILITY}
    elif kind == PUBLIC_QUERY_AMOUNT_KIND:
        spec = PublicQueryAmountSpec.from_dict(value)
        required = {PUBLIC_QUERY_AMOUNT_CAPABILITY}
    elif kind == SCALAR_AMOUNT_KIND:
        spec = ScalarEffectAmountSpec.from_dict(value)
        required = {SCALAR_AMOUNT_CAPABILITY}
    else:
        raise ValueError('Resolution-set stat amount has an unknown quantity kind')
    if getattr(spec, 'binding_id', None) is not None:
        required.add(DECLARED_AMOUNT_CAPABILITY)
    return 2 * spec.coefficient, required


def _chosen_type_temporary_modifier_runtime_dependencies(program):
    from ..resolution_characteristic_model import CHOSEN_CREATURE_TYPE_MODIFIER_FIELD, ChosenCreatureTypeModifier
    from ..query_effect_amount_model import CastXAmountSpec
    from .effect_shape_walkers import nested_effects

    effects = (*program.effects, *program.handlers)
    if not any(CHOSEN_CREATURE_TYPE_MODIFIER_FIELD in child for child in nested_effects(effects)):
        return ()
    if (len(program.effects) != 1 or CHOSEN_CREATURE_TYPE_MODIFIER_FIELD not in program.effects[0]
            or program.target_schema is not None or program.cost_schema is not None or program.handlers):
        raise ValueError("A chosen-type modifier requires its single nontargeted resolution")
    result = ChosenCreatureTypeModifier.from_effect(program.effects[0])
    event = (program.event, program.active_zone)
    if event == ("resolve", "stack") and program.event_condition is None:
        return result.capability_dependencies
    if (event == ("permanent.turned_face_up", "battlefield") and not isinstance(result.amount, CastXAmountSpec)
            and program.event_condition == {"field": "card", "op": "eq", "value": "$source.ref"}):
        return result.capability_dependencies
    raise ValueError("A chosen-type modifier requires its spell or fixed source face-up authority")


def fixed_resolution_characteristics_runtime_dependencies(program):
    """Bind canonical public-set and attachment descriptors independently of declared owners."""
    from .creature_subtype_set_results import creature_subtype_set_runtime_dependencies
    from .source_block_defender import source_block_defender_runtime_dependencies

    if source_block := source_block_defender_runtime_dependencies(program):
        return source_block
    if subtype_set := creature_subtype_set_runtime_dependencies(program):
        return subtype_set
    if chosen_type := _chosen_type_temporary_modifier_runtime_dependencies(program):
        return chosen_type
    from .target_player_stat_untap import target_player_stat_untap_runtime_dependencies

    if target_player := target_player_stat_untap_runtime_dependencies(program):
        return target_player
    if same_name := same_name_creature_stat_runtime_dependencies(program):
        return same_name
    from .event_player_modifiers import event_player_modifier_program_dependencies
    event_player=event_player_modifier_program_dependencies(program)
    if event_player:return event_player
    from ..event_card_return import death_land_exile_runtime_dependencies
    if required := death_land_exile_runtime_dependencies(program):
        return required
    from .effect_shape_walkers import nested_effects
    from ..resolution_characteristic_model import (
        FIXED_SUBTYPE_BASE_CAPABILITY, TARGET_PLAYER_CREATURE_OVERWRITE_VERSION, is_fixed_subtype_base_instruction,
        NEXT_TURN_TARGET_LAND_ANIMATION_MECHANICS,
    )
    from .resolution_characteristic_shapes import (
        is_target_creature_ability_loss, targeted_tap_then_ability_loss_capabilities,
        is_next_turn_target_land_animation, next_turn_land_animation_activation_capabilities,
    )

    if any(is_next_turn_target_land_animation(effect) for effect in nested_effects((program.effects, program.handlers))):
        from ..semantic_runtime.activated_abilities import ActivatedAbilityCatalogHandler

        owners = next_turn_land_animation_activation_capabilities(
            effects=program.effects, target_schema=program.target_schema,
            mechanic_ids=NEXT_TURN_TARGET_LAND_ANIMATION_MECHANICS,
            active_zone=program.active_zone, cost_schema=program.cost_schema,
        )
        if not owners or program.event != "activate" or len(program.handlers) != 1 or program.event_condition is not None:
            raise ValueError("Untap and next-turn land animation require the original +1 loyalty ability")
        ability = ActivatedAbilityCatalogHandler().validate(program.handlers[0])
        if ability.mana_ability or ability.builtin_semantic_key != program.key or ability.loyalty_delta != 1:
            raise ValueError("Land animation must share its own activation catalog")
        return owners

    if any(is_target_creature_ability_loss(effect) for effect in nested_effects((program.effects, program.handlers))):
        owners = targeted_tap_then_ability_loss_capabilities(
            effects=program.effects, target_schema=program.target_schema, mechanic_ids=program.coverage,
        )
        if (not owners or (program.event, program.active_zone) != ("permanent.enter.self", "battlefield")
                or program.handlers or program.cost_schema is not None or program.event_condition is not None):
            raise ValueError("Tap and ability loss require their exact original self-entry trigger")
        return owners

    if any(effect.get("op") == CHARACTERISTIC_OPERATION
           and effect.get("schema_version") == TARGET_PLAYER_CREATURE_OVERWRITE_VERSION
           for effect in nested_effects((program.effects, program.handlers))):
        from .resolution_characteristic_shapes import fixed_resolution_characteristic_capabilities

        owners = fixed_resolution_characteristic_capabilities(
            effects=program.effects, target_schema=program.target_schema, mechanic_ids=program.coverage,
        )
        if (not owners or (program.event, program.active_zone) != ("resolve", "stack")
                or not program.ability_id.startswith("spell:") or program.handlers or program.cost_schema is not None):
            raise ValueError("Player creature overwrite requires its original complete spell resolution")
        return owners

    if any(is_fixed_subtype_base_instruction(effect) for effect in nested_effects((program.effects, program.handlers))):
        from .resolution_characteristic_shapes import fixed_resolution_characteristic_capabilities

        owners = fixed_resolution_characteristic_capabilities(
            effects=program.effects, target_schema=program.target_schema, mechanic_ids=program.coverage,
        )
        if FIXED_SUBTYPE_BASE_CAPABILITY not in owners:
            raise ValueError("Base stats and an added subtype require one complete untargeted creature cohort")
        return owners

    from ..target_land_type_model import (
        FIXED_TARGET_LAND_TYPE_VERSION, FIXED_TARGET_LAND_TYPE_MECHANIC,
        SOURCE_PRESENT_LAND_TYPE_CAPABILITY, SOURCE_EXIT_LAND_TYPE_CAPABILITY,
    )

    if any(effect.get("op") == CHARACTERISTIC_OPERATION
           and effect.get("schema_version") == FIXED_TARGET_LAND_TYPE_VERSION
           for effect in nested_effects((program.effects, program.handlers))):
        from .resolution_characteristic_shapes import fixed_resolution_characteristic_capabilities

        required = fixed_resolution_characteristic_capabilities(
            effects=program.effects, target_schema=program.target_schema,
            mechanic_ids=(FIXED_TARGET_LAND_TYPE_MECHANIC, "cr-611-continuous-effects", "cr-115-targets"),
        )
        if not required:
            raise ValueError("Basic land type setting requires a closed single target and duration")
        if (
            program.active_zone != "battlefield"
            and {SOURCE_PRESENT_LAND_TYPE_CAPABILITY, SOURCE_EXIT_LAND_TYPE_CAPABILITY}.intersection(required)
        ):
            raise ValueError("Source-duration land setting requires a battlefield source")
        return required
    from ..compiler.program_composition import composed_spell_family_runtime_dependencies
    families=[]
    if any(is_attached_characteristic_effect(effect)
            or effect.get('op')=='modify_all_matching_permanents_until_end_of_turn' and 'predicate' in effect
            for effect in program.effects):
        families.append((FIXED_RESOLUTION_CHARACTERISTICS_CAPABILITY,'cr-611-continuous-effects'))
    if any(effect.get('op')==CHARACTERISTIC_OPERATION
            and effect.get('schema_version')==TARGET_CARD_TYPE_ADDITION_VERSION for effect in program.effects):
        families.append((TARGET_CARD_TYPE_ADDITION_CAPABILITY,TARGET_CARD_TYPE_ADDITION_MECHANIC))
    composed_required=set()
    for capability,mechanic in families:
        composed=composed_spell_family_runtime_dependencies(program,capability=capability,mechanic=mechanic)
        if composed is None:break
        composed_required.update(composed)
    else:
        if families:return tuple(sorted(composed_required))
    required = set()
    for effect in program.effects:
        if effect.get("op") == CHARACTERISTIC_OPERATION and effect.get("schema_version") == TARGET_CARD_TYPE_ADDITION_VERSION:
            from .resolution_characteristic_shapes import fixed_resolution_characteristic_capabilities

            owners = fixed_resolution_characteristic_capabilities(
                effects=(effect,), target_schema=program.target_schema, mechanic_ids=program.coverage,
            )
            if not owners:
                raise ValueError("Target card type addition requires closed target and duration authority")
            required.update(owners)
            continue
        if is_attached_characteristic_effect(effect):
            owners = fixed_attached_characteristic_node_capabilities(
                effects=(effect,), target_schema=program.target_schema, mechanic_ids=("cr-611-continuous-effects",),
            )
            if not owners:
                raise ValueError("Attached characteristics require a closed untargeted attachment reference")
            required.update(owners)
            continue
        if effect.get('op') != 'modify_all_matching_permanents_until_end_of_turn' or 'predicate' not in effect:
            continue
        projected = dict(effect)
        for stat in ('power', 'toughness'):
            projected[stat], amount_owners = _runtime_stat_amount(effect.get(stat))
            required.update(amount_owners)
        query=ObjectQuerySpec.from_dict(projected['predicate'])
        if query == CONTROLLED_FLYING_CREATURES_QUERY and any(
            type(effect.get(stat)) is not int for stat in ("power", "toughness")
        ):
            raise ValueError("Flying stat modifiers require their fixed printed deltas")
        selection_schema=program.target_schema if query.controller=='$target.0' else None
        owners = fixed_resolution_characteristic_set_node_capabilities(
            effects=(projected,), target_schema=selection_schema, mechanic_ids=program.coverage,
        )
        if not owners:
            raise ValueError('Resolution-set descriptor lacks complete selection and result closure')
        required.update(owners)
        required.update(cap for keyword in projected.get('keywords', ())
                        for cap in FIXED_CHARACTERISTIC_KEYWORD_CAPABILITIES[keyword])
    return tuple(sorted(required))


__all__ = [
    "FIXED_RESOLUTION_CHARACTERISTICS_CAPABILITY",
    "fixed_attached_stat_node_capabilities",
    "fixed_resolution_characteristic_set_node_capabilities",
    "fixed_resolution_characteristics_runtime_dependencies",
]
