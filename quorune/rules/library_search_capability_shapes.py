from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence

from ..compiler.library_search_templates import (
    FIXED_LIBRARY_SEARCH_CAPABILITY_ID,
    FIXED_LIBRARY_SEARCH_MECHANIC_ID,
    FIXED_LIBRARY_SEARCH_TO_HAND_MECHANIC_ID,
)
from .bargain_search import BARGAIN_SEARCH_CAPABILITY, BARGAIN_SEARCH_MECHANIC, bargain_search_node_capabilities
from ..library_search_model import (
    FIXED_COUNTED_LIBRARY_SEARCH_CAPABILITY_ID,
    FIXED_COUNTED_LIBRARY_SEARCH_MECHANIC_ID,
    DISJUNCTIVE_LIBRARY_SEARCH_CAPABILITY_ID,
    DISJUNCTIVE_LIBRARY_SEARCH_MECHANIC_ID,
    ANY_NUMBER_CREATURE_SUBTYPE_BATTLEFIELD_VERSION, FIXED_CREATURE_GROUP_BATTLEFIELD_SEARCH_VERSION,
    PRINTED_MANA_COST_ARTIFACT_SEARCH_CAPABILITY_ID,
    PRINTED_MANA_COST_ARTIFACT_SEARCH_MECHANIC_ID,
    PRINTED_MANA_COST_ARTIFACT_SEARCH_PREDICATE,
    CREATURE_SUBSET_MANA_VALUE_FIELD, CREATURE_SUBSET_MANA_VALUE_SEARCH_CAPABILITY,
    CREATURE_SUBSET_MANA_VALUE_SEARCH_VERSION, CreatureSubsetManaValueSearch,
    FixedCountedLibrarySearchTemplate,
    RECRUITING_LIBRARY_SEARCH_VERSION,
    TARGET_NAME_LIBRARY_SEARCH_VERSION, TargetNameLibrarySearchSpec,
    TARGET_PLAYER_BASIC_LAND_SEARCH_CAPABILITY,
    TARGET_PLAYER_BASIC_LAND_SEARCH_MECHANIC,
    TARGET_PLAYER_BASIC_LAND_SEARCH_VERSION,
    counted_search_bound_dependencies, counted_search_selector_is_closed,
)
from ..commander_pairing import (
    PARTNER_WITH_SEARCH_CAPABILITY_ID,
    PARTNER_WITH_SEARCH_MECHANIC_ID,
)
from ..named_search_model import (
    LITERAL_NAME_ANY_NUMBER_SEARCH_VERSION, LITERAL_NAME_BATTLEFIELD_SEARCH_VERSION, LiteralNameAnyNumberSearchSpec,
)
from ..object_predicate import ObjectQueryError, ObjectQuerySpec


_SELECTOR_FIELDS = frozenset(
    {"types", "types_any", "subtypes_any", "supertypes", "colors_any"}
)
_PERMANENT_TYPES = frozenset(
    {"artifact", "battle", "creature", "enchantment", "land", "planeswalker"}
)
_CARD_TYPES = _PERMANENT_TYPES | frozenset({"instant", "kindred", "sorcery"})
FIXED_TYPE_TO_HAND_SEARCH_CAPABILITY_ID = "library.search.fixed_type_to_hand"
_FIXED_TYPECYCLING_SELECTORS = (
    {"types": ["land"], "supertypes": ["basic"]},
    *(
        {"types": ["land"], "subtypes_any": [subtype]}
        for subtype in ("plains", "island", "swamp", "mountain", "forest")
    ),
    {"types": ["artifact", "land"]},
    {"subtypes_any": ["wizard"]},
    {"subtypes_any": ["sliver"]},
)


def fixed_library_search_covered_mechanics(
    supplied: Iterable[str],
) -> set[str]:
    """Return search mechanics covered by their exact capability shapes."""

    capabilities = set(supplied)
    from ..exiled_target_library_search import EXILED_TARGET_SEARCH_CAPABILITY, EXILED_TARGET_SEARCH_MECHANIC
    from .targeted_library_exile import (
        TARGET_LIBRARY_EXILE_CAPABILITY, TARGET_LIBRARY_EXILE_MECHANIC,
        TARGET_NAME_MULTIZONE_EXILE_CAPABILITY,
    )
    covered: set[str] = set()
    if EXILED_TARGET_SEARCH_CAPABILITY in capabilities:
        covered.add(EXILED_TARGET_SEARCH_MECHANIC)
    if {TARGET_LIBRARY_EXILE_CAPABILITY, TARGET_NAME_MULTIZONE_EXILE_CAPABILITY}.intersection(capabilities):
        covered.add(TARGET_LIBRARY_EXILE_MECHANIC)
    if 'activation.transmute.hand' in capabilities:
        covered.add('transmute')
    if PRINTED_MANA_COST_ARTIFACT_SEARCH_CAPABILITY_ID in capabilities:
        covered.add(PRINTED_MANA_COST_ARTIFACT_SEARCH_MECHANIC_ID)
    if FIXED_LIBRARY_SEARCH_CAPABILITY_ID in capabilities:
        covered.add(FIXED_LIBRARY_SEARCH_MECHANIC_ID)
    if FIXED_TYPE_TO_HAND_SEARCH_CAPABILITY_ID in capabilities:
        covered.add(FIXED_LIBRARY_SEARCH_TO_HAND_MECHANIC_ID)
    if FIXED_COUNTED_LIBRARY_SEARCH_CAPABILITY_ID in capabilities:
        covered.add(FIXED_COUNTED_LIBRARY_SEARCH_MECHANIC_ID)
    if TARGET_PLAYER_BASIC_LAND_SEARCH_CAPABILITY in capabilities:
        covered.add(TARGET_PLAYER_BASIC_LAND_SEARCH_MECHANIC)
    if BARGAIN_SEARCH_CAPABILITY in capabilities:
        covered.add(BARGAIN_SEARCH_MECHANIC)
    if DISJUNCTIVE_LIBRARY_SEARCH_CAPABILITY_ID in capabilities:
        covered.add(DISJUNCTIVE_LIBRARY_SEARCH_MECHANIC_ID)
    from ..library_search_model import QUERY_BOUND_SEARCH_CAPABILITY,QUERY_BOUND_SEARCH_MECHANIC
    if QUERY_BOUND_SEARCH_CAPABILITY in capabilities:covered.add(QUERY_BOUND_SEARCH_MECHANIC)
    return covered


def fixed_counted_library_search_node_capabilities(
    *, effects: Sequence[Mapping[str, object]],
    target_schema: Mapping[str, object] | None,
    mechanic_ids: Iterable[str],
) -> tuple[str, ...]:
    if FIXED_COUNTED_LIBRARY_SEARCH_MECHANIC_ID not in mechanic_ids or len(effects) != 1:
        return ()
    if effects[0].get("schema_version") == TARGET_NAME_LIBRARY_SEARCH_VERSION:
        try:
            spec = TargetNameLibrarySearchSpec.from_effect(effects[0])
        except (TypeError, ValueError):
            return ()
        return spec.capabilities() if target_schema == spec.target_schema() else ()
    if target_schema is not None:
        return ()
    if effects[0].get("schema_version") in (
        LITERAL_NAME_ANY_NUMBER_SEARCH_VERSION, LITERAL_NAME_BATTLEFIELD_SEARCH_VERSION, RECRUITING_LIBRARY_SEARCH_VERSION,
        ANY_NUMBER_CREATURE_SUBTYPE_BATTLEFIELD_VERSION, FIXED_CREATURE_GROUP_BATTLEFIELD_SEARCH_VERSION,
        CREATURE_SUBSET_MANA_VALUE_SEARCH_VERSION,
    ):
        try:
            return counted_search_bound_dependencies(effects[0])
        except (TypeError, ValueError):
            return ()
    if effects[0].get('schema_version') == 8:
        from ..library_search_model import QueryBoundLibrarySearchSpec,QUERY_BOUND_SEARCH_MECHANIC
        if QUERY_BOUND_SEARCH_MECHANIC not in mechanic_ids:return ()
        try:return QueryBoundLibrarySearchSpec.from_effect(effects[0]).capabilities()
        except (ValueError,TypeError,KeyError):return ()
    if effects[0].get('schema_version') == 7:
        from ..qualified_library_search_model import QualifiedLibrarySearchSpec, QUALIFIED_LIBRARY_SEARCH_MECHANIC
        if QUALIFIED_LIBRARY_SEARCH_MECHANIC not in mechanic_ids:return ()
        try:return QualifiedLibrarySearchSpec.from_effect(effects[0]).capabilities()
        except (ValueError, TypeError, KeyError):return ()
    if effects[0].get('schema_version') == 5:
        from ..search_allocation_model import SearchAllocationSpec, SEARCH_ALLOCATION_MECHANIC
        if SEARCH_ALLOCATION_MECHANIC not in mechanic_ids:return ()
        try:return SearchAllocationSpec.from_effect(effects[0]).capabilities()
        except (ValueError, TypeError, KeyError):return ()
    if effects[0].get('schema_version')==4:
        from ..conditional_search_model import ConditionalSearchSpec,CONDITIONAL_SEARCH_MECHANIC
        if CONDITIONAL_SEARCH_MECHANIC not in mechanic_ids:return ()
        try:return ConditionalSearchSpec.from_effect(effects[0]).capabilities()
        except (ValueError,TypeError,KeyError):return ()
    try:
        template = FixedCountedLibrarySearchTemplate.from_effect(effects[0])
    except (ValueError, TypeError, KeyError):
        return ()
    if template.up_to_two_forest_batch:
        from ..library_search_model import UP_TO_TWO_FOREST_BATCH_CAPABILITY

        return (FIXED_COUNTED_LIBRARY_SEARCH_CAPABILITY_ID, UP_TO_TWO_FOREST_BATCH_CAPABILITY)
    if template.disjunctive:
        if DISJUNCTIVE_LIBRARY_SEARCH_MECHANIC_ID not in mechanic_ids:
            return ()
        return (FIXED_COUNTED_LIBRARY_SEARCH_CAPABILITY_ID, DISJUNCTIVE_LIBRARY_SEARCH_CAPABILITY_ID)
    if template.different_names:
        from ..library_search_model import DIFFERENT_NAME_CREATURE_SEARCH_CAPABILITY

        return (FIXED_COUNTED_LIBRARY_SEARCH_CAPABILITY_ID, DIFFERENT_NAME_CREATURE_SEARCH_CAPABILITY)
    return (FIXED_COUNTED_LIBRARY_SEARCH_CAPABILITY_ID,)


def _query(
    selector: object,
    *,
    permanent_only: bool,
) -> ObjectQuerySpec | None:
    if (
        not isinstance(selector, Mapping)
        or not selector
        or not set(selector).issubset(_SELECTOR_FIELDS)
        or not counted_search_selector_is_closed(
            selector, destination="battlefield" if permanent_only else "hand",
        )
    ):
        return None
    try:
        query = ObjectQuerySpec(
            zones=("library",),
            types_all=tuple(selector.get("types") or ()),
            types_any=tuple(selector.get("types_any") or ()),
            subtypes_any=tuple(selector.get("subtypes_any") or ()),
            supertypes_all=tuple(selector.get("supertypes") or ()),
            colors_any=tuple(selector.get("colors_any") or ()),
        )
    except (ObjectQueryError, TypeError):
        return None
    if not (
        query.types_all
        or query.types_any
        or query.subtypes_any
        or query.supertypes_all
        or query.colors_any
    ):
        return None
    allowed_types = _PERMANENT_TYPES if permanent_only else _CARD_TYPES
    if not set((*query.types_all, *query.types_any)).issubset(allowed_types):
        return None
    if permanent_only and not (query.types_all or query.types_any):
        return None
    return query


def target_name_library_search_runtime_dependencies(program):
    from .effect_shape_walkers import nested_effects
    from .targeted_library_exile import TARGET_NAME_MULTIZONE_EXILE_VERSION, TargetNameMultizoneExileSpec

    supported_versions = (TARGET_NAME_LIBRARY_SEARCH_VERSION, TARGET_NAME_MULTIZONE_EXILE_VERSION)
    if not any(effect.get("op") == "search" and (
        effect.get("schema_version") in supported_versions or "name_target" in effect
    ) for effect in nested_effects((program.effects, program.handlers))):
        return ()
    if ((program.event, program.active_zone) != ("resolve", "stack")
            or program.handlers or program.cost_schema is not None or len(program.effects) != 1):
        raise ValueError("Target-name search requires its exact single spell resolution")
    effect = program.effects[0]
    if effect.get("schema_version") == TARGET_NAME_MULTIZONE_EXILE_VERSION:
        spec = TargetNameMultizoneExileSpec.from_effect(effect)
        if program.event_condition is not None or program.target_schema != spec.target_schema():
            raise ValueError("Multizone name search requires its exact printed permanent target")
        return spec.capabilities()
    spec = TargetNameLibrarySearchSpec.from_effect(effect)
    if program.target_schema != spec.target_schema():
        raise ValueError("Target-name search requires the original creature target")
    return spec.capabilities()


def literal_name_any_number_runtime_dependencies(program):
    from .effect_shape_walkers import nested_effects

    if not any(effect.get("op") == "search" and effect.get("schema_version") == LITERAL_NAME_ANY_NUMBER_SEARCH_VERSION
               for effect in nested_effects((program.effects, program.handlers))):
        return ()
    if ((program.event, program.active_zone) != ("permanent.enter.self", "battlefield")
            or program.target_schema is not None or program.cost_schema is not None
            or program.event_condition is not None or program.handlers or len(program.effects) != 1):
        raise ValueError("An optional any-number named search requires its original entry trigger")
    wrapper = program.effects[0]
    effects = wrapper.get("effects")
    if (set(wrapper) != {"op", "player", "effects"}
            or wrapper["op"] != "offer_optional_effect" or wrapper["player"] != "$controller"
            or not isinstance(effects, (list, tuple)) or len(effects) != 1):
        raise ValueError("The resolving controller must opt into this private library search")
    LiteralNameAnyNumberSearchSpec.from_effect(effects[0])
    return (FIXED_COUNTED_LIBRARY_SEARCH_CAPABILITY_ID, "effect.choice.optional_fixed",
            "trigger.event.normalized_zone_change")


def recruiting_library_search_runtime_dependencies(program):
    from .effect_shape_walkers import nested_effects

    if not any(effect.get("op") == "search" and effect.get("schema_version") == RECRUITING_LIBRARY_SEARCH_VERSION
               for effect in nested_effects((program.effects, program.handlers))):
        return ()
    if ((program.event, program.active_zone) != ("permanent.enter.self", "battlefield")
            or program.target_schema is not None or program.cost_schema is not None
            or program.event_condition is not None or program.handlers or len(program.effects) != 1
            or program.effects[0].get("op") != "search"
            or program.effects[0].get("schema_version") != RECRUITING_LIBRARY_SEARCH_VERSION):
        raise ValueError("Recruiting search requires its exact mandatory source-entry result")
    return (*counted_search_bound_dependencies(program.effects[0]), "trigger.event.normalized_zone_change")


def target_player_basic_land_search_node_capabilities(
    *, effects: Sequence[Mapping[str, object]], target_schema: Mapping[str, object] | None,
) -> tuple[str, ...]:
    """Bind a versioned search to exactly one required player target."""
    if not isinstance(target_schema, Mapping) or type(target_schema.get("count")) is not int:
        return ()
    if dict(target_schema) != {
        "zones": ["player"], "categories": ["player"], "player_relation": "any", "count": 1,
    } or len(effects) != 1:
        return ()
    effect = effects[0]
    if not isinstance(effect, Mapping) or effect.get("schema_version") != TARGET_PLAYER_BASIC_LAND_SEARCH_VERSION:
        return ()
    try:
        return counted_search_bound_dependencies(effect)
    except (TypeError, ValueError):
        return ()


def target_player_basic_land_search_runtime_dependencies(program):
    """Recheck the complete activation and target binding when loading its program."""
    from .effect_shape_walkers import nested_effects

    if (TARGET_PLAYER_BASIC_LAND_SEARCH_CAPABILITY not in program.capability_dependencies
            and TARGET_PLAYER_BASIC_LAND_SEARCH_MECHANIC not in program.coverage
            and not any(effect.get("op") == "search"
                        and effect.get("schema_version") == TARGET_PLAYER_BASIC_LAND_SEARCH_VERSION
                        for effect in nested_effects((program.effects, program.handlers)))):
        return ()
    from ..semantic_runtime.activated_abilities import (
        ACTIVATED_ABILITY_CATALOG_HANDLER_ID, activated_abilities_from_descriptors,
    )

    if (program.event != "activate" or program.cost_schema is None or len(program.handlers) != 1
            or program.handlers[0].get("handler_id") != ACTIVATED_ABILITY_CATALOG_HANDLER_ID):
        raise ValueError("Targeted basic-land search requires its original activation catalog")
    abilities = activated_abilities_from_descriptors(program.handlers)
    if len(abilities) != 1 or program.ability_id != f"ability:{abilities[0].ability_id}":
        raise ValueError("Targeted basic-land search requires its original activation identity")
    required = target_player_basic_land_search_node_capabilities(
        effects=program.effects, target_schema=program.target_schema,
    )
    if not required:
        raise ValueError("Targeted basic-land search requires its exact result and required player target")
    return required


def creature_subset_mana_value_search_runtime_dependencies(program):
    from .effect_shape_walkers import contains_nested_key, nested_effects

    values = (program.effects, program.handlers, program.cost_schema, program.target_schema, program.event_condition)
    if (
        CREATURE_SUBSET_MANA_VALUE_SEARCH_CAPABILITY not in program.capability_dependencies
        and not contains_nested_key(values, CREATURE_SUBSET_MANA_VALUE_FIELD)
        and not any(
            effect["op"] == "search" and effect.get("schema_version") == CREATURE_SUBSET_MANA_VALUE_SEARCH_VERSION
            for effect in nested_effects(values)
        )
    ):
        return ()
    if (
        (program.event, program.active_zone) != ("creature.dies.self", "battlefield")
        or program.event_condition is not None or program.target_schema is not None
        or program.cost_schema is not None or program.trigger_limit is not None
        or program.handlers or len(program.effects) != 1
    ):
        raise ValueError("Creature subset search requires its exact untargeted source-death trigger")
    CreatureSubsetManaValueSearch.from_effect(program.effects[0])
    return (
        FIXED_COUNTED_LIBRARY_SEARCH_CAPABILITY_ID, CREATURE_SUBSET_MANA_VALUE_SEARCH_CAPABILITY,
        "trigger.event.normalized_zone_change",
    )


def creature_stat_library_search_runtime_dependencies(program):
    from ..qualified_library_search_model import (
        CREATURE_STAT_LIBRARY_SEARCH_CAPABILITY, CREATURE_STAT_QUALIFICATION_VERSION,
        QUALIFIED_LIBRARY_SEARCH_VERSION, QualifiedLibrarySearchSpec,
    )
    from ..target_numeric import TargetNumericCharacteristic
    from .effect_shape_walkers import nested_effects

    if (
        CREATURE_STAT_LIBRARY_SEARCH_CAPABILITY not in program.capability_dependencies
        and not any(
            isinstance(qualification := effect.get("qualification"), Mapping)
            and (
                "stat_bound" in qualification
                or (
                    effect.get("op") == "search"
                    and effect.get("schema_version") == QUALIFIED_LIBRARY_SEARCH_VERSION
                    and qualification.get("schema_version") == CREATURE_STAT_QUALIFICATION_VERSION
                    and set(qualification) != {"schema_version", "query", "keywords_any"}
                )
            )
            for effect in nested_effects((program.effects, program.handlers))
        )
    ):
        return ()
    if (
        (program.event, program.active_zone) != ("permanent.enter.self", "battlefield")
        or program.target_schema is not None or program.cost_schema is not None
        or program.event_condition is not None or program.trigger_limit is not None
        or program.handlers or len(program.effects) != 1
    ):
        raise ValueError("Creature stat search requires its exact untargeted source-entry trigger")
    effect = program.effects[0]
    optional = effect.get("op") == "offer_optional_effect"
    if optional:
        children = effect.get("effects")
        if (
            set(effect) != {"op", "player", "effects"} or effect.get("player") != "$controller"
            or not isinstance(children, (tuple, list)) or len(children) != 1
        ):
            raise ValueError("The original resolving controller may accept the creature stat search")
        effect = children[0]
    spec = QualifiedLibrarySearchSpec.from_effect(effect)
    if spec.stat_bound is None or optional != (spec.stat_bound.characteristic is TargetNumericCharacteristic.TOUGHNESS):
        raise ValueError("Creature stat search requires its originally printed optional or mandatory instruction")
    return (
        *spec.capabilities(), "trigger.event.normalized_zone_change",
        *(("effect.choice.optional_fixed",) if optional else ()),
    )


def fixed_library_search_node_capabilities(
    *,
    effects: Sequence[Mapping[str, object]],
    target_schema: Mapping[str, object] | None,
    mechanic_ids: Iterable[str],
) -> tuple[str, ...]:
    """Recognize one compiler-owned fixed search-to-battlefield instruction."""

    from .exiled_target_search_shapes import exiled_target_search_node_capabilities
    from .targeted_library_exile import targeted_library_exile_node_capabilities
    from .search_random_discard import private_search_random_discard_node_capabilities

    if private_search := private_search_random_discard_node_capabilities(
        effects=effects, target_schema=target_schema, mechanic_ids=mechanic_ids,
    ):
        return private_search
    if combined := exiled_target_search_node_capabilities(effects=effects, target_schema=target_schema, mechanic_ids=mechanic_ids):
        return combined
    targeted = targeted_library_exile_node_capabilities(effects=effects,target_schema=target_schema,mechanic_ids=mechanic_ids)
    if targeted:return targeted
    mechanics = {str(value).casefold() for value in mechanic_ids}
    if TARGET_PLAYER_BASIC_LAND_SEARCH_MECHANIC in mechanics:
        return target_player_basic_land_search_node_capabilities(effects=effects, target_schema=target_schema)
    if BARGAIN_SEARCH_MECHANIC in mechanics:
        return bargain_search_node_capabilities(effects=effects, target_schema=target_schema, mechanic_ids=mechanics)
    if FIXED_COUNTED_LIBRARY_SEARCH_MECHANIC_ID in mechanics:
        return fixed_counted_library_search_node_capabilities(effects=effects, target_schema=target_schema, mechanic_ids=mechanics)
    if (
        FIXED_LIBRARY_SEARCH_MECHANIC_ID not in mechanics
        or target_schema is not None
        or len(effects) != 1
    ):
        return ()
    effect = effects[0]
    required = {
        "op",
        "zone",
        "selector",
        "count",
        "destination",
        "shuffle_after",
    }
    if set(effect) not in (required, {*required, "enters_tapped_override"}):
        return ()
    count = effect.get("count")
    if not isinstance(count, Mapping) or set(count) != {"minimum", "maximum"}:
        return ()
    minimum = count.get("minimum")
    maximum = count.get("maximum")
    if (
        type(minimum) is not int
        or type(maximum) is not int
        or not 0 <= minimum <= maximum <= 10
        or maximum == 0
        or minimum not in {0, maximum}
    ):
        return ()
    selector = effect.get("selector")
    printed_cost_predicate = (
        isinstance(selector, Mapping)
        and selector.get("predicate") == PRINTED_MANA_COST_ARTIFACT_SEARCH_PREDICATE
    )
    if printed_cost_predicate:
        types = selector.get("types")
        if (
            PRINTED_MANA_COST_ARTIFACT_SEARCH_MECHANIC_ID not in mechanics
            or set(selector) != {"types", "predicate"}
            or not isinstance(types, (list, tuple))
            or len(types) != 1
            or types[0] != "artifact"
        ):
            return ()
        query_selector = {"types": types}
    else:
        if (
            isinstance(selector, Mapping)
            and "predicate" in selector
        ):
            return ()
        query_selector = selector
    query = _query(query_selector, permanent_only=True)
    if (
        effect.get("op") != "search"
        or effect.get("zone") != "library"
        or effect.get("destination") != "battlefield"
        or effect.get("shuffle_after") is not True
        or effect.get("enters_tapped_override", True) is not True
        or query is None
    ):
        return ()
    if maximum > 1 and not (
        effect.get("enters_tapped_override") is True
        and query.types_all == ("land",)
        and not query.types_any
    ):
        return ()
    if printed_cost_predicate:
        return (
            FIXED_LIBRARY_SEARCH_CAPABILITY_ID,
            PRINTED_MANA_COST_ARTIFACT_SEARCH_CAPABILITY_ID,
        )
    return (FIXED_LIBRARY_SEARCH_CAPABILITY_ID,)


def fixed_type_to_hand_search_node_capabilities(
    *,
    effects: Sequence[Mapping[str, object]],
    target_schema: Mapping[str, object] | None,
    mechanic_ids: Iterable[str],
) -> tuple[str, ...]:
    """Recognize one closed fixed restrictive type search to hand."""

    mechanics = {str(value).casefold() for value in mechanic_ids}
    if (
        not mechanics.intersection(
            {"cycling", FIXED_LIBRARY_SEARCH_TO_HAND_MECHANIC_ID}
        )
        or target_schema is not None
        or len(effects) != 1
    ):
        return ()
    effect = effects[0]
    if set(effect) != {
        "op",
        "zone",
        "selector",
        "count",
        "destination",
        "reveal",
        "shuffle_after",
    }:
        return ()
    count = effect.get("count")
    selector = effect.get("selector")
    generic_search = FIXED_LIBRARY_SEARCH_TO_HAND_MECHANIC_ID in mechanics
    if (
        effect.get("op") != "search"
        or effect.get("zone") != "library"
        or effect.get("destination") != "hand"
        or type(effect.get("reveal")) is not bool
        or (not generic_search and effect.get("reveal") is not True)
        or effect.get("shuffle_after") is not True
        or not isinstance(count, Mapping)
        or set(count) != {"minimum", "maximum"}
        or type(count.get("minimum")) is not int
        or type(count.get("maximum")) is not int
        or count.get("maximum") != 1
        or count.get("minimum") not in {0, 1}
        or (
            not generic_search
            and dict(count) != {"minimum": 1, "maximum": 1}
        )
        or not isinstance(selector, Mapping)
        or _query(selector, permanent_only=False) is None
        or (
            not generic_search
            and dict(selector) not in _FIXED_TYPECYCLING_SELECTORS
        )
    ):
        return ()
    return (FIXED_TYPE_TO_HAND_SEARCH_CAPABILITY_ID,)


def partner_with_search_node_capabilities(
    *,
    effects: Sequence[Mapping[str, object]],
    target_schema: Mapping[str, object] | None,
    mechanic_ids: Iterable[str],
) -> tuple[str, ...]:
    """Recognize the optional named search intrinsic to Partner with."""

    mechanics = {str(value).casefold() for value in mechanic_ids}
    if (
        PARTNER_WITH_SEARCH_MECHANIC_ID not in mechanics
        or target_schema
        != {
            "zones": ["player"],
            "categories": ["player"],
            "player_relation": "any",
            "count": 1,
        }
        or len(effects) != 1
    ):
        return ()
    effect = effects[0]
    if set(effect) != {
        "op",
        "searching_player",
        "zone",
        "selector",
        "count",
        "destination",
        "optional",
        "shuffle_after",
    }:
        return ()
    selector = effect.get("selector")
    names = selector.get("names") if isinstance(selector, Mapping) else None
    if (
        effect.get("op") != "search"
        or effect.get("searching_player") != "$target.0"
        or effect.get("zone") != "library"
        or not isinstance(selector, Mapping)
        or set(selector) != {"names"}
        or not isinstance(names, list)
        or len(names) != 1
        or type(names[0]) is not str
        or not names[0].strip()
        or names[0] != names[0].strip()
        or effect.get("count") != {"minimum": 0, "maximum": 1}
        or effect.get("destination") != "hand"
        or effect.get("optional") is not True
        or effect.get("shuffle_after") is not True
    ):
        return ()
    return (
        PARTNER_WITH_SEARCH_CAPABILITY_ID,
        "target.revalidate_resolution",
    )


__all__ = [
    "FIXED_LIBRARY_SEARCH_CAPABILITY_ID",
    "FIXED_LIBRARY_SEARCH_MECHANIC_ID",
    "FIXED_LIBRARY_SEARCH_TO_HAND_MECHANIC_ID",
    "FIXED_TYPE_TO_HAND_SEARCH_CAPABILITY_ID",
    "PARTNER_WITH_SEARCH_CAPABILITY_ID",
    "PARTNER_WITH_SEARCH_MECHANIC_ID",
    "fixed_library_search_covered_mechanics",
    "fixed_library_search_node_capabilities",
    "target_player_basic_land_search_node_capabilities",
    "fixed_counted_library_search_node_capabilities",
    "fixed_type_to_hand_search_node_capabilities",
    "partner_with_search_node_capabilities",
]
