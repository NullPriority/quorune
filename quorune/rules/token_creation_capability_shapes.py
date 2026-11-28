from __future__ import annotations

"""Strict capability shape for fixed-definition token instructions."""

import re
from typing import Any, Mapping, Sequence

from ..source_entry_cast_information import SOURCE_ENTRY_CAST_HAND_MECHANIC, SOURCE_ENTRY_CAST_MECHANIC
from ..token_copy_recipient_model import (
    OCCURRENCE_COPY_RECIPIENT_CAPABILITY, OCCURRENCE_COPY_RECIPIENT_MECHANIC, has_occurrence_copy_recipient,
)
from ..token_copy_recipes import (
    TOKEN_COPY_BATCH_CAPABILITY, TOKEN_COPY_BATCH_FIELD, TOKEN_COPY_BATCH_MECHANIC,
    TokenCopyBatchSpec, TokenCopyRecipeError, TokenCopyRecipeSpec,
)

from ..ability_fragment_primitives import CrewPowerBonusSpec
from ..ability_fragments import (
    AbilityFragmentError,
    AllCreatureTypesCharacteristicDefinitionSpec,
    CombatKeywordTriggerSpec,
    GrantedActivatedAbilitySpec,
    GrantedTriggeredAbilitySpec,
    QueryCharacteristicModifierSpec,
    QueryPowerToughnessDefinitionSpec,
    SpellCastKeywordTriggerKind,
    SpellCastKeywordTriggerSpec,
    ToxicSpec,
    canonical_ability_fragments,
    decayed_ability_fragments,
)
from ..adventure import ADVENTURE_COVERAGE
from ..cast_face_offers import OMEN_COVERAGE
from ..keyword_abilities import triggered_keyword_grant
from ..compiler.token_templates import (
    FIXED_TOKEN_DEFINITION_BATCH_MECHANIC,
    fixed_token_creation_effect_template,
)
from ..compiler.direct_target import DirectPermanentTargetSpec, direct_permanent_target_spec
from ..declaration_fragments import (
    DECLARATION_COMPONENT_CAPABILITY_ID,
    DeclarationRequirementTemplate,
    DeclarationRestrictionTemplate,
)
from ..fixed_token_production import (
    AFTERLIFE_CAPABILITY_ID,
    AFTERLIFE_MECHANIC_ID,
    FIXED_DELAYED_TOKEN_CAPABILITY_ID,
    FIXED_TOKEN_COPY_CAPABILITY_ID,
    FIXED_TOKEN_PRODUCTION_MECHANIC_ID,
    FIXED_DISTRIBUTED_TOKEN_CAPABILITY_ID,
    FIXED_DISTRIBUTED_TOKEN_MECHANIC_ID,
    FIXED_DISTRIBUTED_TOKEN_OPERATION,
    FixedTokenCreatorScope,
    fixed_token_creator_scope,
    fixed_token_effect_shape_is_closed,
    FixedTokenProductionError,
    INVESTIGATE_CAPABILITY_ID,
    INVESTIGATE_MECHANIC_ID,
    SEQUENTIAL_INVESTIGATE_OPERATION,
    TYPED_TOKEN_ABILITY_CAPABILITY_ID,
    TYPED_TOKEN_ABILITY_MECHANIC_ID,
    afterlife_token_effect,
    clue_token_effect,
    sequential_investigate_count,
    fixed_token_player_target_schema,
    TOKEN_LITERAL_NAME_VERSION_FIELD,
    literal_token_name_version,
    fixed_ordinary_token_name,
    historical_unnamed_token_view,
)


_TOKEN_MECHANIC = "cr-111-tokens"
_COLOR_ORDER = "WUBRG"


def _is_fixed_token_action_effect(effect: Mapping[str, Any], build_effect) -> bool:
    from ..replacement.immutable import thaw_value

    quantity = effect.get("quantity")
    characteristics = effect.get("characteristics")
    if type(quantity) is not int or not isinstance(characteristics, Mapping):
        return False
    try:
        legacy_name = literal_token_name_version(characteristics) is None
        return thaw_value(effect) == build_effect(quantity, legacy_name=legacy_name)
    except FixedTokenProductionError:
        return False


def query_defined_token_stat_dependencies(
    characteristics: Mapping[str, Any],
) -> tuple[str, ...]:
    """Require a unique intrinsic definition for every star-valued token stat."""
    fields = tuple(field for field in ("power", "toughness") if characteristics.get(field) == "*")
    if not fields:
        return ()
    definitions = tuple(
        fragment for fragment in canonical_ability_fragments(characteristics.get("ability_fragments", ()))
        if isinstance(fragment, QueryPowerToughnessDefinitionSpec)
    )
    if any(sum(getattr(definition, f"define_{field}") for definition in definitions) != 1 for field in fields):
        raise FixedTokenProductionError("Star-valued token stats require one intrinsic definition per stat")
    return (
        TYPED_TOKEN_ABILITY_CAPABILITY_ID,
        "continuous.characteristics.query_power_toughness_definition",
    )


CAST_PERMANENT_TOKEN_COPY_CAPABILITY = "token.creation.cast_source_permanent_copy"


def cast_spell_token_copy_payload_dependencies(value: Any) -> tuple[str, ...]:
    """A cast-source token has a separate raw minimum from battlefield copy recipes."""
    if not isinstance(value, Mapping) or value.get("op") != "create_token":
        return ()
    spec = value.get("copy_spec")
    if not isinstance(spec, Mapping) or spec.get("schema_version") != 4:
        return ()
    if (set(value) != {"op","controller","quantity","copy_spec"} or value["controller"] != "$controller"
            or type(value["quantity"]) is not int or value["quantity"] != 1):
        raise ValueError("Direct cast spell copy creates one token for its resolving controller")
    TokenCopyRecipeSpec.from_dict(spec)
    from ..event_spell_copy_model import EVENT_SPELL_COPY_CAPABILITY
    return (CAST_PERMANENT_TOKEN_COPY_CAPABILITY, FIXED_TOKEN_COPY_CAPABILITY_ID, EVENT_SPELL_COPY_CAPABILITY)


def created_token_haste_closure(effect):
    from ..fixed_token_production import (
        CREATED_TOKEN_HASTE_CAPABILITY, CREATED_TOKEN_HASTE_FIELD, CREATED_TOKEN_HASTE_LIFELINK_CAPABILITY,
        created_token_keyword_grant, temporary_haste_lifelink_goblin_effect,
    )
    from ..replacement.immutable import FrozenMap

    if not isinstance(effect, Mapping) or type(effect.get(CREATED_TOKEN_HASTE_FIELD)) is not int:
        raise ValueError("Created-token haste requires its explicit instruction version")
    keywords = created_token_keyword_grant(effect[CREATED_TOKEN_HASTE_FIELD])
    base = {key: value for key, value in effect.items() if key != CREATED_TOKEN_HASTE_FIELD}
    if not fixed_token_effect_shape_is_closed(base) or (
        len(keywords) == 2 and FrozenMap(effect) != FrozenMap(temporary_haste_lifelink_goblin_effect())
    ):
        raise ValueError("Created-token keywords require their closed creature token instruction")
    creation = _fixed_creature_effect_closure(base)
    if creation is None:
        raise ValueError("Created-token haste requires a closed creature token definition")
    required, mechanics = creation
    capability = CREATED_TOKEN_HASTE_CAPABILITY if len(keywords) == 1 else CREATED_TOKEN_HASTE_LIFELINK_CAPABILITY
    return (*required, capability, "continuous.resolution.fixed_keyword_zone_object"), mechanics


def token_definition_payload_dependencies(value: Any, mechanics: Sequence[str] = ()) -> tuple[str, ...]:
    """Infer fixed token and selected creator dependencies from the payload.

    Copy recipes keep their separate owner. Legacy token references without an
    explicit definition retain their existing compatibility path.
    """
    from ..fixed_token_production import CREATED_TOKEN_HASTE_FIELD

    if isinstance(value, Mapping) and CREATED_TOKEN_HASTE_FIELD in value:
        if "token_cleanup" in value:
            from .created_token_cleanup_shapes import created_token_cleanup_closure

            return created_token_cleanup_closure(value)[0]
        return created_token_haste_closure(value)[0]
    if isinstance(value, Mapping) and value.get('op')=='choose_option':
        from .fixed_token_choices import fixed_token_choice_payload_dependencies
        return fixed_token_choice_payload_dependencies(value,mechanics)
    if isinstance(value, Mapping) and value.get("op") == FIXED_DISTRIBUTED_TOKEN_OPERATION:
        return distributed_token_creation_payload_dependencies(value)
    if isinstance(value, Mapping) and TOKEN_COPY_BATCH_FIELD in value:
        return TokenCopyBatchSpec.from_effect(value).capability_dependencies
    from .cast_target_copy import cast_target_copy_payload_dependencies
    cast_copy = cast_spell_token_copy_payload_dependencies(value) or cast_target_copy_payload_dependencies(value)
    if cast_copy:
        return cast_copy
    from ..token_copy_recipes import EQUIPPED_TOKEN_COPY_CAPABILITY, EQUIPPED_TOKEN_COPY_VERSION

    if (isinstance(value, Mapping) and value.get("op") == "create_token"
            and isinstance(value.get("copy_spec"), Mapping)
            and value["copy_spec"].get("schema_version") in {3, EQUIPPED_TOKEN_COPY_VERSION}):
        recipe = TokenCopyRecipeSpec.from_dict(value["copy_spec"])
        if recipe.schema_version == EQUIPPED_TOKEN_COPY_VERSION:
            if (set(value) != {"op", "controller", "quantity", "copy_spec"}
                    or value["controller"] != "$controller" or type(value["quantity"]) is not int or value["quantity"] != 1):
                raise ValueError("Equipped copy requires exactly one original controller token")
            return (EQUIPPED_TOKEN_COPY_CAPABILITY, FIXED_TOKEN_COPY_CAPABILITY_ID,
                    "object.reference.source_attachment_information", "continuous.resolution.fixed_keyword_zone_object")
        return ("token.creation.enchanted_source_copy", FIXED_TOKEN_COPY_CAPABILITY_ID,
                "object.reference.source_attachment_information")
    targeted = (
        isinstance(value, Mapping)
        and value.get("op") == "create_token"
        and "target" in value
    )
    target_dependencies = ("target.revalidate_resolution",) if targeted else ()
    from .fixed_basic_land_token import fixed_basic_land_token_payload_dependencies

    land = fixed_basic_land_token_payload_dependencies(value)
    if land:
        return land
    if (isinstance(value, Mapping) and value.get('op') == 'create_token'
            and isinstance(value.get('characteristics'), Mapping)
            and re.fullmatch(r'Token (?:Legendary )?Artifact — (?:Book )?Equipment',
                str(value['characteristics'].get('type_line'))) is not None):
        from .equipment_token_shapes import fixed_equipment_token_closure
        closure = fixed_equipment_token_closure(value)
        if closure is None:
            raise ValueError('Fixed Equipment token definition has an unrepresented payload')
        return (*closure[0], *target_dependencies)
    if (isinstance(value,Mapping) and value.get('op')=='create_token'
            and isinstance(value.get('characteristics'),Mapping)
            and {'power','toughness'}<=set(value['characteristics'])
            and not value.get('copy_of') and not value.get('copy_spec')):
        if AFTERLIFE_MECHANIC_ID in mechanics and _is_fixed_token_action_effect(value, afterlife_token_effect):
            return (AFTERLIFE_CAPABILITY_ID,)
        land_dependencies = ()
        if 'Land' in str(value['characteristics'].get('type_line', '')).split(' — ', 1)[0].split():
            if _fixed_creature_effect_closure(value) is None:
                raise ValueError('Land creature token definition has an unrepresented payload')
            land_dependencies = ('mana.intrinsic.basic_land_type',)
        return (
            'token.creation.fixed_definition',
            *land_dependencies,
            *token_toxic_definition_dependencies(value['characteristics']),
            *token_decayed_definition_dependencies(value['characteristics']),
            *query_defined_token_stat_dependencies(value['characteristics']),
            *target_dependencies,
        )
    return target_dependencies


def token_toxic_definition_dependencies(characteristics: Mapping[str, Any]) -> tuple[str, ...]:
    """Require one typed instance for each printed positive fixed Toxic value."""
    keywords = characteristics.get("keywords", ())
    raw = characteristics.get("ability_fragments", ())
    if not isinstance(keywords, (tuple, list)) or not isinstance(raw, (tuple, list)):
        raise ValueError("Token keyword and fragment arrays are malformed")
    fragments = canonical_ability_fragments(raw)
    toxic = sorted(fragment.value for fragment in fragments if isinstance(fragment, ToxicSpec))
    printed = []
    for keyword in keywords:
        if not isinstance(keyword, str):
            raise ValueError("Token keyword must be text")
        if keyword.casefold().startswith("toxic"):
            match = re.fullmatch(r"Toxic ([1-9]\d*)", keyword)
            if match is None:
                raise ValueError("Token Toxic must have a canonical positive fixed value")
            printed.append(int(match[1]))
    if sorted(printed) != toxic or len(printed) != len(set(printed)):
        raise ValueError("Token Toxic keywords require matching typed instances")
    return ("damage.result.toxic",) if toxic else ()


def token_decayed_definition_dependencies(
    characteristics: Mapping[str, Any],
) -> tuple[str, ...]:
    """Require both typed abilities for an ordinary Decayed token definition."""

    keywords = characteristics.get("keywords", ())
    fragments = canonical_ability_fragments(characteristics.get("ability_fragments", ()))
    printed = sum(
        isinstance(keyword, str) and keyword.casefold() == "decayed"
        for keyword in keywords
    )
    trigger, block_restriction = decayed_ability_fragments()
    instances = fragments.count(trigger)
    if printed != instances or printed > 1 or (printed and block_restriction not in fragments):
        raise ValueError("Token Decayed requires matching attack and block abilities")
    return (
        ("combat.trigger.decayed", DECLARATION_COMPONENT_CAPABILITY_ID)
        if printed else ()
    )


_CREATURE_KEYWORDS = frozenset(
    {
        "Banding",
        "Deathtouch",
        "Decayed",
        "Defender",
        "Double Strike",
        "First Strike",
        "Flying",
        "Forestwalk",
        "Haste",
        "Hexproof",
        "Indestructible",
        "Infect",
        "Islandwalk",
        "Lifelink",
        "Mountainwalk",
        "Menace",
        "Plainswalk",
        "Reach",
        "Swampwalk",
        "Trample",
        "Vigilance",
        "Changeling",
        "Prowess",
    }
)
_PREDEFINED_CAPABILITIES = {
    "Lander": ("token.ability.fixed_lander",),
    "Mutagen": ("token.ability.fixed_mutagen",),
    "Blood": ("token.ability.fixed_blood",),
    "Treasure": ("mana.activated.fixed_output",),
    "Gold": ("mana.activated.fixed_output",),
    "Food": ("life.change.effect",),
    "Map": ("keyword_action.explore.single",),
    "Clue": ("zone.draw.library_to_hand",),
    "Powerstone": ("mana.activated.restricted_fixed_output",),
    "Junk": ("zone.impulse_access.fixed",),
    "Vibranium": (
        "mana.activated.restricted_fixed_output",
        "permanent.indestructible.ordinary",
    ),
}
_AUXILIARY_MECHANICS = frozenset(
    {
        'fixed-kicked-entry-trigger',
        'declared-cast-cost-fact',
        SOURCE_ENTRY_CAST_MECHANIC,
        SOURCE_ENTRY_CAST_HAND_MECHANIC,
        "activated_ability",
        "granted_activated_ability",
        "granted_triggered_ability",
        "cr-601-casting-spells",
        "cr-603-handling-triggered-abilities",
        "exhaust",
        "fixed-typed-event-effect-trigger",
        "fixed-delayed-token-creation",
        "fixed-token-copy",
        FIXED_TOKEN_PRODUCTION_MECHANIC_ID,
        INVESTIGATE_MECHANIC_ID,
        AFTERLIFE_MECHANIC_ID,
        "generated_oracle_ir",
        "spell_resolution",
        ADVENTURE_COVERAGE,
        OMEN_COVERAGE,
        "triggered_ability",
        TYPED_TOKEN_ABILITY_MECHANIC_ID,
        "cr-115-targets",
        "cr-707-copying-objects",
    }
)


def _token_specific_mechanics(mechanics: set[str]) -> set[str]:
    """Exclude independently gated trigger and activation scaffolding."""

    return {
        mechanic
        for mechanic in mechanics
        if mechanic not in _AUXILIARY_MECHANICS
        and not mechanic.startswith("trigger-event-")
    }


def _fixed_predefined_effect_is_closed(
    effect: Mapping[str, Any],
) -> tuple[tuple[str, ...], set[str]] | None:
    name = effect.get("name")
    characteristics = effect.get("characteristics")
    if not isinstance(name, str) or not isinstance(characteristics, Mapping):
        return None
    former = historical_unnamed_token_view(name, characteristics)
    base_name = former[0] if former is not None else name
    if base_name not in _PREDEFINED_CAPABILITIES:
        return None
    expected = fixed_token_creation_effect_template(
        f"Create a {base_name} token."
    )
    if expected is None:
        return None
    canonical = dict(expected.effect)
    if former is None:
        legacy = historical_unnamed_token_view(canonical["name"], canonical["characteristics"])
        if legacy is None:
            return None
        canonical["name"], canonical["characteristics"] = legacy
    supplied = dict(effect)
    supplied["quantity"] = 1
    supplied.pop("tapped", None)
    if supplied != canonical:
        return None
    keywords = characteristics.get("keywords", [])
    return (
        (
            "token.creation.fixed_definition",
            *_PREDEFINED_CAPABILITIES[base_name],
        ),
        {_TOKEN_MECHANIC, *(str(value).casefold() for value in keywords)},
    )


def _fixed_creature_definition_fields_are_closed(
    characteristics: Mapping[str, Any],
) -> bool:
    allowed = {
        "type_line",
        "colors",
        "power",
        "toughness",
        "keywords",
        "ability_fragments",
        TOKEN_LITERAL_NAME_VERSION_FIELD,
    }
    required = {"type_line", "colors", "power", "toughness"}
    if not required.issubset(characteristics) or set(characteristics) - allowed:
        return False
    colors = characteristics.get("colors")
    if (
        not isinstance(colors, list)
        or len(colors) > 3
        or any(color not in _COLOR_ORDER for color in colors)
        or colors != sorted(set(colors), key=_COLOR_ORDER.index)
    ):
        return False
    for field in ("power", "toughness"):
        value = characteristics.get(field)
        if not isinstance(value, str) or (value != "*" and not value.isdigit()):
            return False
    return True


def _fixed_creature_effect_closure(
    effect: Mapping[str, Any],
) -> tuple[tuple[str, ...], set[str]] | None:
    characteristics = effect.get("characteristics")
    if (
        not isinstance(characteristics, Mapping)
        or not _fixed_creature_definition_fields_are_closed(characteristics)
    ):
        return None
    try:
        name_version = literal_token_name_version(characteristics)
    except FixedTokenProductionError:
        return None
    name = effect.get("name")
    if not isinstance(name, str) or re.fullmatch(
        r"[A-Z][A-Za-z']*(?:[ -](?:[A-Z][A-Za-z']*|the|of|and|to|a|an|in|on|at|for))*", name
    ) is None:
        return None
    type_line = str(characteristics.get("type_line") or "")
    if re.fullmatch(
        r"Token (?:Legendary )?(?:Artifact )?(?:Land )?Creature(?: Enchantment)? — "
        r"[A-Z][A-Za-z']*(?:[ -][A-Z][A-Za-z']*)*",
        type_line,
    ) is None:
        return None
    keywords = characteristics.get("keywords", [])
    if (
        not isinstance(keywords, list)
        or any(
            not isinstance(keyword, str)
            or (keyword not in _CREATURE_KEYWORDS and re.fullmatch(r"Toxic [1-9]\d*", keyword) is None and triggered_keyword_grant(keyword) is None)
            for keyword in keywords
        )
        or len(keywords) != len(set(keywords))
    ):
        return None
    capabilities = {"token.creation.fixed_definition"}
    if "Land" in type_line.split(" — ", 1)[0]:
        capabilities.add("mana.intrinsic.basic_land_type")
    mechanics = {
        _TOKEN_MECHANIC,
        *(keyword.split()[0].casefold() if keyword.startswith(("Toxic ","Bushido ")) else keyword.casefold() for keyword in keywords),
    }
    subtype_text = type_line.split(" — ", 1)[1]
    default_name = fixed_ordinary_token_name(subtype_text) if name_version is not None else subtype_text
    if name != default_name or "Legendary" in type_line:
        mechanics.add("fixed-named-token")
    raw_fragments = characteristics.get("ability_fragments", [])
    if not isinstance(raw_fragments, list) or len(raw_fragments) > 3:
        return None
    try:
        fragments = canonical_ability_fragments(raw_fragments)
    except (AbilityFragmentError, TypeError, ValueError):
        return None
    if len(fragments) != len(raw_fragments):
        return None
    try:
        capabilities.update(token_toxic_definition_dependencies(characteristics))
        capabilities.update(token_decayed_definition_dependencies(characteristics))
        capabilities.update(query_defined_token_stat_dependencies(characteristics))
    except (AbilityFragmentError, TypeError, ValueError):
        return None
    from .token_prowess_requirements import token_triggered_keyword_definition_dependencies, token_combat_fragment_is_declared
    try:capabilities.update(token_triggered_keyword_definition_dependencies(characteristics))
    except (TypeError,ValueError):return None
    typed_ability_definition = False
    typed_ability_fragment = False
    decayed_trigger, decayed_restriction = decayed_ability_fragments()
    for fragment in fragments:
        if isinstance(fragment, ToxicSpec):
            capabilities.add("damage.result.toxic")
        elif isinstance(fragment, AllCreatureTypesCharacteristicDefinitionSpec):
            if "Changeling" not in keywords:
                return None
            capabilities.add("continuous.characteristics.changeling")
        elif isinstance(fragment, SpellCastKeywordTriggerSpec):
            if fragment.kind is not SpellCastKeywordTriggerKind.PROWESS or 'Prowess' not in keywords:
                return None
            capabilities.add('trigger.keyword.prowess')
        elif isinstance(fragment, CombatKeywordTriggerSpec):
            if fragment != decayed_trigger and not token_combat_fragment_is_declared(fragment,keywords):
                return None
        elif isinstance(fragment, DeclarationRestrictionTemplate):
            if "Decayed" in keywords and fragment == decayed_restriction:
                continue
            legacy_declaration = fragment.template_id in {
                "intrinsic-block-prohibition-v1",
                "intrinsic-unblockable-v1",
            }
            if not legacy_declaration:
                typed_ability_definition = True
            if not legacy_declaration and not fragment.mechanics:
                return None
            capabilities.add("combat.declaration.typed_components")
            mechanics.add("fixed-token-declaration-fragment")
            mechanics.update(fragment.mechanics)
            typed_ability_fragment = typed_ability_definition
        elif isinstance(fragment, DeclarationRequirementTemplate):
            typed_ability_definition = True
            capabilities.add("combat.declaration.typed_components")
            mechanics.add("fixed-token-declaration-fragment")
            mechanics.update(fragment.mechanics)
            typed_ability_fragment = True
        elif isinstance(fragment, QueryCharacteristicModifierSpec):
            typed_ability_definition = True
            capabilities.add("continuous.characteristics.query_count_modifier")
            typed_ability_fragment = True
        elif isinstance(fragment, CrewPowerBonusSpec):
            typed_ability_definition = True
            capabilities.add(fragment.capability_id)
            typed_ability_fragment = True
        elif isinstance(fragment, QueryPowerToughnessDefinitionSpec):
            typed_ability_definition = True
            capabilities.add(
                "continuous.characteristics.query_power_toughness_definition"
            )
            typed_ability_fragment = True
        elif isinstance(
            fragment,
            (GrantedActivatedAbilitySpec, GrantedTriggeredAbilitySpec),
        ):
            typed_ability_definition = True
            if (
                ":token:" not in fragment.ability_id
                or not fragment.semantic_key.endswith(
                    f":{fragment.ability_id}"
                )
            ):
                return None
            if isinstance(fragment, GrantedActivatedAbilitySpec) and fragment.capability_id is not None:
                capabilities.add(fragment.capability_id)
            typed_ability_fragment = True
        else:
            return None
    if "Changeling" in keywords and not any(
        isinstance(fragment, AllCreatureTypesCharacteristicDefinitionSpec)
        for fragment in fragments
    ):
        return None
    if typed_ability_definition:
        if not typed_ability_fragment:
            return None
        capabilities.add(TYPED_TOKEN_ABILITY_CAPABILITY_ID)
    return tuple(sorted(capabilities)), mechanics


def _fixed_token_effect_closure(
    effect: Mapping[str, Any],
) -> tuple[tuple[str, ...], set[str]] | None:
    if not fixed_token_effect_shape_is_closed(effect):
        return None
    from ..fixed_mana_results import FIXED_MANA_LIFE_GAIN_CAPABILITY
    from ..fixed_token_production import TOKEN_MANA_LIFE_PROFILE, named_mana_life_token

    if effect["characteristics"].get("activated_ability_profile") == TOKEN_MANA_LIFE_PROFILE:
        try:
            if effect != named_mana_life_token(effect["name"]):
                return None
        except FixedTokenProductionError:
            return None
        return ("token.creation.fixed_definition", FIXED_MANA_LIFE_GAIN_CAPABILITY), {_TOKEN_MECHANIC, "fixed-named-token"}
    predefined = _fixed_predefined_effect_is_closed(effect)
    if predefined is not None:
        return predefined
    from .equipment_token_shapes import fixed_equipment_token_closure
    equipment = fixed_equipment_token_closure(effect)
    if equipment is not None:
        return equipment
    from .fixed_basic_land_token import fixed_basic_land_token_closure

    return fixed_basic_land_token_closure(effect) or _fixed_creature_effect_closure(effect)


def _fixed_copy_token_capabilities(
    effect: Mapping[str, Any],
    target_schema: Mapping[str, Any] | None,
    mechanic_ids: set[str],
) -> tuple[str, ...]:
    required_mechanics = {
        _TOKEN_MECHANIC,
        FIXED_TOKEN_PRODUCTION_MECHANIC_ID,
        "fixed-token-copy",
        "cr-115-targets",
        "cr-707-copying-objects",
    }
    if (
        set(effect) != {"op", "controller", "quantity", "copy_of"}
        or effect.get("op") != "create_token"
        or effect.get("controller") != "$controller"
        or effect.get("quantity") != 1
        or effect.get("copy_of") != "$target.0"
        or not isinstance(target_schema, Mapping)
        or not required_mechanics.issubset(mechanic_ids)
    ):
        return ()
    specs = tuple(
        direct_permanent_target_spec(subject)
        for subject in (
            "target creature",
            "target artifact, creature, or land",
        )
    )
    if any(spec is None for spec in specs):
        return ()
    schemas = tuple(spec.to_target_schema() for spec in specs if spec is not None)
    return (
        (FIXED_TOKEN_COPY_CAPABILITY_ID,)
        if dict(target_schema) in schemas
        else ()
    )


def graveyard_target_token_copy_runtime_dependencies(program):
    from ..token_copy_recipes import GRAVEYARD_TARGET_TOKEN_COPY_ORIGIN, GRAVEYARD_TARGET_TOKEN_COPY_VERSION
    from .effect_shape_walkers import nested_effects

    def uses_graveyard_recipe(node):
        recipe = node.get("copy_spec")
        return isinstance(recipe, Mapping) and (
            recipe.get("schema_version") == GRAVEYARD_TARGET_TOKEN_COPY_VERSION
            or recipe.get("origin") == GRAVEYARD_TARGET_TOKEN_COPY_ORIGIN
        )

    if not any(uses_graveyard_recipe(effect) for effect in nested_effects((program.effects, program.handlers))):
        return ()
    if ((program.event, program.active_zone) != ("activate", "battlefield")
            or program.event_condition is not None or program.cost_schema is None
            or len(program.effects) != 1 or not uses_graveyard_recipe(program.effects[0])
            or any(handler.get("handler_id") != "activation.catalog.pinned.v1" for handler in program.handlers)):
        raise ValueError("Graveyard token copy needs one complete battlefield activation")
    required = _copy_recipe_capabilities(program.effects[0], program.target_schema, set(program.coverage))
    if not required:
        raise ValueError("Graveyard token copy needs its exact current own-creature target and full lifecycle")
    return required


def post_entry_copy_grant_runtime_dependencies(program):
    from ..token_copy_recipes import POST_ENTRY_COPY_GRANT_VERSION
    from .effect_shape_walkers import nested_effects

    def uses_post_entry_grant(effect):
        recipe = effect.get("copy_spec")
        return isinstance(recipe, Mapping) and (
            recipe.get("schema_version") == POST_ENTRY_COPY_GRANT_VERSION or "post_entry_grant" in recipe
        )

    if not any(uses_post_entry_grant(effect) for effect in nested_effects((program.effects, program.handlers))):
        return ()
    if ((program.event, program.active_zone) != ("activate", "battlefield")
            or program.cost_schema is None or program.event_condition is not None
            or len(program.effects) != 1 or not uses_post_entry_grant(program.effects[0])
            or any(handler.get("handler_id") != "activation.catalog.pinned.v1" for handler in program.handlers)):
        raise ValueError("Copy with a post-entry grant requires one complete battlefield activation")
    required = _copy_recipe_capabilities(program.effects[0], program.target_schema, set(program.coverage))
    if not required:
        raise ValueError("Copy with a post-entry grant requires its exact controlled creature target and lifecycle")
    grant = TokenCopyRecipeSpec.from_dict(program.effects[0]["copy_spec"]).post_entry_grant
    if grant.semantic_key != f"{program.oracle_id}:{grant.ability_id}":
        raise ValueError("Copy's post-entry grant must belong to its own Oracle program")
    return required


def _copy_recipe_capabilities(effect, target_schema, mechanics):
    if has_occurrence_copy_recipient(effect):
        from .token_copy_recipients import occurrence_copy_recipient_node_dependencies

        return occurrence_copy_recipient_node_dependencies(effect, target_schema, mechanics)
    if 'copy_recipient_binding' in effect:
        from .token_copy_recipients import target_copy_recipient_node_dependencies
        return target_copy_recipient_node_dependencies(effect, target_schema, mechanics)
    from ..token_copy_recipes import (
        GRAVEYARD_TARGET_TOKEN_COPY_ORIGIN, TokenCopyRecipeSpec, graveyard_target_token_copy_schema,
    )
    from ..keyword_abilities import FIXED_CHARACTERISTIC_KEYWORD_CAPABILITIES
    from .permanent_predicate_capability_shapes import direct_permanent_target_schema_is_closed, direct_target_predicate_capabilities
    fields = {"op", "controller", "quantity", "copy_spec"}
    if set(effect) not in (fields, fields | {"tapped"}) or effect.get("op") != "create_token" or effect.get("controller") != "$controller":
        return ()
    if type(effect.get("quantity")) is not int or not 1 <= effect["quantity"] <= 5 or type(effect.get("tapped", False)) is not bool:
        return ()
    try:
        recipe = TokenCopyRecipeSpec.from_dict(effect["copy_spec"])
    except (TypeError, ValueError):
        return ()
    if recipe.origin == "target":
        if not direct_permanent_target_schema_is_closed(target_schema) or target_schema.get("count") != 1:
            return ()
    elif recipe.origin == GRAVEYARD_TARGET_TOKEN_COPY_ORIGIN:
        from ..replacement.immutable import thaw_value

        if (not isinstance(target_schema, Mapping) or thaw_value(target_schema) != graveyard_target_token_copy_schema()
                or type(target_schema.get("count")) is not int or set(effect) != fields or effect["quantity"] != 1):
            return ()
    elif target_schema is not None:
        return ()
    required = {_TOKEN_MECHANIC, FIXED_TOKEN_PRODUCTION_MECHANIC_ID, "fixed-token-copy", "cr-707-copying-objects"}
    if not required.issubset(mechanics):
        return ()
    if set(recipe.temporary_keywords) - {"Haste"}:
        return ()
    dependencies = {FIXED_TOKEN_COPY_CAPABILITY_ID}
    if recipe.origin in {"event_spell", "cast_target"}:
        from .cast_target_copy import cast_target_copy_payload_dependencies
        try:
            dependencies.update(cast_spell_token_copy_payload_dependencies(effect) or cast_target_copy_payload_dependencies(effect))
        except (TypeError, ValueError):
            return ()
    if recipe.origin == "source_attachment":
        try:
            dependencies.update(token_definition_payload_dependencies(effect, mechanics))
        except (TypeError, ValueError, KeyError):
            return ()
    if recipe.creation_origin=='embalm':dependencies.add('activation.graveyard_copy_keyword.fixed_mana')
    if recipe.origin == GRAVEYARD_TARGET_TOKEN_COPY_ORIGIN:
        dependencies.add("target.revalidate_resolution")
    if recipe.origin == "target":
        dependencies.add("target.revalidate_resolution")
        dependencies.update(direct_target_predicate_capabilities(target_schema))
    for keyword in (*recipe.exception.add_keywords, *recipe.temporary_keywords):
        dependencies.update(FIXED_CHARACTERISTIC_KEYWORD_CAPABILITIES[keyword])
    if recipe.temporary_keywords:
        dependencies.add("continuous.resolution.fixed_keyword_zone_object" if recipe.keyword_duration == "zone_object"
                         else "continuous.resolution.fixed_characteristics_until_end_of_turn")
    if recipe.source_bound_cleanup:
        from ..replacement.immutable import thaw_value

        target = DirectPermanentTargetSpec(
            types_any=("artifact",), types_none=("creature",), controller_relation="you",
        ).to_target_schema()
        if set(effect) != fields or effect["quantity"] != 1 or thaw_value(target_schema) != target:
            return ()
        dependencies.update({"trigger.delayed.fixed_object_action", "zone.move.fixed_public_set"})
    if recipe.cleanup != "none":
        dependencies.add("trigger.placement.apnap")
    fragments = canonical_ability_fragments(recipe.exception.add_ability_fragments)
    if recipe.post_entry_grant is not None:
        from ..replacement.immutable import thaw_value

        target = {"zones": ["battlefield"], "categories": ["permanent"], "count": 1,
                  "types_any": ["creature"], "controller_relation": "you", "source_exclusion": True}
        if set(effect) != fields or effect["quantity"] != 1 or thaw_value(target_schema) != target:
            return ()
        dependencies.update({"trigger.delayed.fixed_object_action", "zone.move.fixed_public_set"})
        fragments = (recipe.post_entry_grant,)
    if bool(fragments) != (TYPED_TOKEN_ABILITY_MECHANIC_ID in mechanics):
        return ()
    if fragments:
        for fragment in fragments:
            if not isinstance(fragment, (GrantedActivatedAbilitySpec, GrantedTriggeredAbilitySpec)):
                return ()
            if ":token:" not in fragment.ability_id or not fragment.semantic_key.endswith(f":{fragment.ability_id}"):
                return ()
            if isinstance(fragment, GrantedActivatedAbilitySpec) and fragment.capability_id is not None:
                dependencies.add(fragment.capability_id)
        dependencies.add(TYPED_TOKEN_ABILITY_CAPABILITY_ID)
    return tuple(sorted(dependencies))


def _fixed_delayed_token_capabilities(
    effect: Mapping[str, Any],
    mechanic_ids: set[str],
) -> tuple[str, ...]:
    label = "Create fixed token at the beginning of the next end step"
    required_mechanics = {
        _TOKEN_MECHANIC,
        FIXED_TOKEN_PRODUCTION_MECHANIC_ID,
        "fixed-delayed-token-creation",
        "cr-603-handling-triggered-abilities",
    }
    if (
        set(effect)
        != {
            "op",
            "controller",
            "label",
            "event",
            "condition",
            "stack",
            "once",
        }
        or effect.get("op") != "delayed_trigger"
        or effect.get("controller") != "$controller"
        or effect.get("label") != label
        or effect.get("event") != "step.begin"
        or effect.get("condition")
        != {"phase": "ending", "step": "end_step"}
        or effect.get("once") is not True
        or not required_mechanics.issubset(mechanic_ids)
    ):
        return ()
    stack = effect.get("stack")
    if not isinstance(stack, Mapping) or set(stack) != {"label", "context"}:
        return ()
    context = stack.get("context")
    if (
        stack.get("label") != label
        or not isinstance(context, Mapping)
        or set(context) != {"dynamic_effects"}
    ):
        return ()
    dynamic = context.get("dynamic_effects")
    if (
        not isinstance(dynamic, list)
        or len(dynamic) != 1
        or not isinstance(dynamic[0], Mapping)
    ):
        return ()
    closure = _fixed_token_effect_closure(dynamic[0])
    if closure is None:
        return ()
    nested_capabilities, expected_mechanics = closure
    return (
        tuple(
            dict.fromkeys(
                (FIXED_DELAYED_TOKEN_CAPABILITY_ID, *nested_capabilities)
            )
        )
        if _token_specific_mechanics(mechanic_ids) == expected_mechanics
        else ()
    )


def _distributed_token_creation_closure(
    effect: Mapping[str, Any],
) -> tuple[FixedTokenCreatorScope, tuple[str, ...], set[str]]:
    scope = fixed_token_creator_scope(effect)
    if scope is FixedTokenCreatorScope.CONTROLLER_AND_CHOSEN_OPPONENT:
        raise FixedTokenProductionError("A chosen creator requires the resolving choice owner")
    targeted = scope is FixedTokenCreatorScope.CONTROLLER_AND_TARGET_OPPONENT
    if effect["controller"] != "$controller" or targeted and effect["target"] != "$target.0":
        raise FixedTokenProductionError("Distributed token creation requires bound creators")
    child = _fixed_token_effect_closure(effect["token"])
    if child is None:
        raise FixedTokenProductionError("Distributed token creation requires one closed token definition")
    child_dependencies, mechanics = child
    dependencies = tuple(dict.fromkeys((
        FIXED_DISTRIBUTED_TOKEN_CAPABILITY_ID, *child_dependencies,
        *(("target.revalidate_resolution",) if targeted else ()),
    )))
    return scope, dependencies, {*mechanics, FIXED_DISTRIBUTED_TOKEN_MECHANIC_ID}


def distributed_token_creation_payload_dependencies(effect: Mapping[str, Any]) -> tuple[str, ...]:
    from .qualified_token_creators import project_qualified_token_creators, uses_qualified_token_creators

    if uses_qualified_token_creators(effect):
        effect = project_qualified_token_creators(effect)
    token = effect.get("token")
    if isinstance(token, Mapping) and isinstance(token.get("quantity"), Mapping):
        from ..scalar_effect_amount_model import ScalarAmountOrigin, ScalarEffectAmountSpec

        amount = ScalarEffectAmountSpec.from_dict(token["quantity"])
        if (effect.get("scope") != FixedTokenCreatorScope.EACH_PLAYER.value
                or amount.origin is not ScalarAmountOrigin.SOURCE or amount.counter_name is None
                or amount.binding_id is None or amount.coefficient != 1):
            raise FixedTokenProductionError("Distributed token quantity requires a shared source counter amount")
        effect = {**effect, "token": {**token, "quantity": 2}}
    return _distributed_token_creation_closure(effect)[1]


def _fixed_distributed_token_node_capabilities(
    effect: Mapping[str, Any],
    target_schema: Mapping[str, Any] | None,
    mechanics: set[str],
) -> tuple[str, ...]:
    try:
        scope, dependencies, expected_mechanics = _distributed_token_creation_closure(effect)
    except (ValueError, TypeError, KeyError):
        return ()
    targeted = scope is FixedTokenCreatorScope.CONTROLLER_AND_TARGET_OPPONENT
    if target_schema != (fixed_token_player_target_schema("opponent") if targeted else None):
        return ()
    if targeted and "cr-115-targets" not in mechanics:
        return ()
    return dependencies if _token_specific_mechanics(mechanics) == expected_mechanics else ()


def _fixed_token_definition_node_capabilities(
    effect: Mapping[str, Any],
    target_schema: Mapping[str, Any] | None,
    mechanics: set[str],
) -> tuple[str, ...]:
    """Validate one ordinary token definition and any selected creator."""
    from ..fixed_token_production import CREATED_TOKEN_HASTE_FIELD
    from ..reflexive_trigger_model import TOKEN_CREATION_REFLEXIVE_FIELD

    if CREATED_TOKEN_HASTE_FIELD in effect:
        if target_schema is not None:
            return ()
        try:
            required, expected = created_token_haste_closure(effect)
        except (TypeError, ValueError):
            return ()
        return required if expected.issubset(mechanics) else ()
    if TOKEN_CREATION_REFLEXIVE_FIELD in effect:
        from .token_creation_reflexive import token_creation_reflexive_node_capabilities
        return token_creation_reflexive_node_capabilities(effects=(effect,), target_schema=target_schema,
            mechanic_ids=mechanics)
    fixed_effect = effect
    if target_schema is not None:
        if not isinstance(target_schema, Mapping):
            return ()
        relation = target_schema.get("player_relation")
        if (
            effect.get("target") != "$target.0"
            or "controller" in effect
            or "cr-115-targets" not in mechanics
            or relation not in ("any", "opponent")
            or type(target_schema.get("count")) is not int
            or dict(target_schema) != fixed_token_player_target_schema(relation)
        ):
            return ()
        fixed_effect = dict(effect)
        del fixed_effect["target"]
        fixed_effect["controller"] = "$controller"
    if (
        INVESTIGATE_MECHANIC_ID in mechanics
        and {
            _TOKEN_MECHANIC,
            FIXED_TOKEN_PRODUCTION_MECHANIC_ID,
        }.issubset(mechanics)
        and _is_fixed_token_action_effect(effect, clue_token_effect)
    ):
        return (INVESTIGATE_CAPABILITY_ID,)
    if (
        AFTERLIFE_MECHANIC_ID in mechanics
        and {
            _TOKEN_MECHANIC,
            FIXED_TOKEN_PRODUCTION_MECHANIC_ID,
            "cr-603-handling-triggered-abilities",
        }.issubset(mechanics)
        and _is_fixed_token_action_effect(effect, afterlife_token_effect)
    ):
        return (AFTERLIFE_CAPABILITY_ID,)
    closure = _fixed_token_effect_closure(fixed_effect)
    if closure is None:
        return ()
    capabilities, expected_mechanics = closure
    typed_ability = TYPED_TOKEN_ABILITY_CAPABILITY_ID in capabilities
    if typed_ability != (
        TYPED_TOKEN_ABILITY_MECHANIC_ID in mechanics
    ):
        return ()
    if _token_specific_mechanics(mechanics) == expected_mechanics:
        if target_schema is not None:
            return (*capabilities, "target.revalidate_resolution")
        return capabilities
    return ()


def _copy_batch_capabilities(effect, target_schema, mechanics):
    from ..replacement.immutable import thaw_value

    required = {_TOKEN_MECHANIC, FIXED_TOKEN_PRODUCTION_MECHANIC_ID, "fixed-token-copy",
                "cr-707-copying-objects", TOKEN_COPY_BATCH_MECHANIC}
    try:
        spec = TokenCopyBatchSpec.from_effect(effect)
    except TokenCopyRecipeError:
        return ()
    if spec.targets_player:
        required.add("cr-115-targets")
        if not isinstance(target_schema, Mapping) or type(target_schema.get("count")) is not int:
            return ()
    if not required <= mechanics or spec.target_schema != thaw_value(target_schema):
        return ()
    return spec.capability_dependencies


def chosen_entry_token_copy_program_dependencies(program):
    from ..controller_creature_keyword_absence import is_controller_end_step_schedule
    from ..token_copy_recipes import uses_chosen_entry_token_copy
    from .effect_shape_walkers import nested_effects

    if not any(uses_chosen_entry_token_copy(effect) for effect in nested_effects((program.effects, program.handlers))):
        return ()
    if (len(program.effects) != 1
            or (program.event, program.active_zone) != ("step.begin", "battlefield")
            or not is_controller_end_step_schedule(program.event_condition)
            or program.target_schema is not None or program.cost_schema is not None or program.handlers
            or program.destination is not None or program.trigger_limit is not None or program.requires_arbiter
            or not program.ability_id.startswith("trigger:")):
        raise TokenCopyRecipeError("Chosen-type token copies require the original controller end-step program")
    spec = TokenCopyBatchSpec.from_effect(program.effects[0])
    if not spec.uses_chosen_entry_models:
        raise TokenCopyRecipeError("Chosen-type token copies require the closed same-turn token query")
    return spec.capability_dependencies


def source_bound_populate_capabilities(effect):
    from ..fixed_token_production import source_bound_populate_effect
    from ..keyword_abilities import FIXED_CHARACTERISTIC_KEYWORD_CAPABILITIES

    if (not isinstance(effect, Mapping) or effect != source_bound_populate_effect()
            or type(effect["schema_version"]) is not int):
        raise ValueError("Source-bound Populate requires exactly its separate haste and next-end-step result")
    return (FIXED_TOKEN_COPY_CAPABILITY_ID, "continuous.resolution.fixed_keyword_zone_object",
            "trigger.delayed.fixed_object_action", "trigger.placement.apnap", "zone.move.fixed_public_set",
            *FIXED_CHARACTERISTIC_KEYWORD_CAPABILITIES["Haste"])


def source_bound_populate_runtime_dependencies(program):
    from ..fixed_token_production import uses_source_bound_populate
    from .effect_shape_walkers import nested_effects

    if any(uses_source_bound_populate(effect) for effect in nested_effects(program.effects)):
        from ..replacement.immutable import thaw_value

        condition = {"all": [
            {"field": "player", "op": "eq", "value": "$source.controller"},
            {"field": "step", "op": "eq", "value": "beginning_combat"},
        ]}
        if (len(program.effects) != 1 or program.target_schema is not None or program.cost_schema is not None
                or program.handlers or program.destination is not None or program.trigger_limit is not None
                or program.requires_arbiter or (program.event, program.active_zone) != ("step.begin", "battlefield")
                or thaw_value(program.event_condition) != condition):
            raise ValueError("Source-bound Populate requires its complete untargeted beginning-of-combat trigger")
        return source_bound_populate_capabilities(program.effects[0])
    return ()


def fixed_token_creation_node_capabilities(
    *,
    effects: Sequence[Mapping[str, Any]],
    target_schema: Mapping[str, Any] | None,
    mechanic_ids: set[str],
) -> tuple[str, ...]:
    if 'fixed-token-definition-choice' in mechanic_ids:
        from .fixed_token_choices import fixed_token_choice_node_capabilities
        return fixed_token_choice_node_capabilities(effects=effects,target_schema=target_schema,mechanic_ids=mechanic_ids)
    if {'paid-entry-copy-offspring', 'paid-entry-copy-squad'} & set(mechanic_ids):
        from .paid_entry_copy_shapes import paid_entry_copy_node_capabilities
        return paid_entry_copy_node_capabilities(effects=effects, target_schema=target_schema, mechanic_ids=mechanic_ids)
    if 'predefined-role-token' in mechanic_ids:
        from .role_token_shapes import role_token_node_capabilities
        return role_token_node_capabilities(effects=effects, target_schema=target_schema, mechanics=set(mechanic_ids))
    """Recognize a compiler-owned fixed token creation instruction."""

    from .controlled_creature_counter_tokens import COUNT_FIELD, controlled_creature_counter_token_capabilities

    if len(effects) == 1 and COUNT_FIELD in effects[0]:
        return controlled_creature_counter_token_capabilities(effects[0], target_schema, mechanic_ids)
    from .created_token_cleanup_shapes import created_token_cleanup_node_capabilities
    cleanup = created_token_cleanup_node_capabilities(effects=effects, target_schema=target_schema, mechanic_ids=mechanic_ids)
    if cleanup:
        return cleanup
    from .attacking_token_results import attacking_token_node_capabilities
    attacking = attacking_token_node_capabilities(effects=effects, target_schema=target_schema, mechanic_ids=mechanic_ids)
    if attacking:
        return attacking
    from .attack_batches import attacking_opponent_token_effects_are_closed, ATTACKING_OPPONENT_TOKEN_CAPABILITY
    if target_schema is None and attacking_opponent_token_effects_are_closed(effects):
        required = _fixed_token_definition_node_capabilities(effects[0], None, set(mechanic_ids))
        return (*required, ATTACKING_OPPONENT_TOKEN_CAPABILITY) if required else ()
    if len(effects) != 1:
        return ()
    effect = effects[0]
    mechanics = set(mechanic_ids)
    if TOKEN_COPY_BATCH_FIELD in effect:
        return _copy_batch_capabilities(effect, target_schema, mechanics)
    if effect.get("op") == SEQUENTIAL_INVESTIGATE_OPERATION:
        required = {INVESTIGATE_MECHANIC_ID, FIXED_TOKEN_PRODUCTION_MECHANIC_ID, _TOKEN_MECHANIC}
        if target_schema is not None or not required.issubset(mechanics):
            return ()
        try:
            sequential_investigate_count(effect)
        except FixedTokenProductionError:
            return ()
        return (INVESTIGATE_CAPABILITY_ID,)
    if effect.get("op") == "populate" and "schema_version" in effect:
        from ..fixed_token_production import ANNOUNCED_POPULATE_CAPABILITY, announced_populate_count, uses_source_bound_populate
        from ..query_effect_amount_model import DECLARED_AMOUNT_CAPABILITY

        required = {_TOKEN_MECHANIC, FIXED_TOKEN_PRODUCTION_MECHANIC_ID, "fixed-token-copy", "cr-707-copying-objects"}
        if target_schema is not None or not required.issubset(mechanics):
            return ()
        try:
            if uses_source_bound_populate(effect):
                return source_bound_populate_capabilities(effect)
            announced_populate_count(effect)
        except (TypeError, ValueError, KeyError):
            return ()
        return (FIXED_TOKEN_COPY_CAPABILITY_ID, ANNOUNCED_POPULATE_CAPABILITY, DECLARED_AMOUNT_CAPABILITY)
    if dict(effect) == {"op":"populate"} and target_schema is None:
        required = {_TOKEN_MECHANIC, FIXED_TOKEN_PRODUCTION_MECHANIC_ID, "fixed-token-copy", "cr-707-copying-objects"}
        return (FIXED_TOKEN_COPY_CAPABILITY_ID,) if required.issubset(mechanics) else ()
    if effect.get("op") == "delayed_trigger":
        if target_schema is not None:
            return ()
        return _fixed_delayed_token_capabilities(effect, mechanics)
    if effect.get("copy_spec") is not None:
        return _copy_recipe_capabilities(effect, target_schema, mechanics)
    if effect.get("copy_of") is not None:
        capabilities = _fixed_copy_token_capabilities(
            effect,
            target_schema,
            mechanics,
        )
        return (
            capabilities
            if capabilities
            and _token_specific_mechanics(mechanics) == {_TOKEN_MECHANIC}
            else ()
        )
    if effect.get("op") == "create_token":
        return _fixed_token_definition_node_capabilities(effect, target_schema, mechanics)
    if effect.get("op") == FIXED_DISTRIBUTED_TOKEN_OPERATION:
        return _fixed_distributed_token_node_capabilities(effect, target_schema, mechanics)
    if (
        target_schema is not None
        or set(effect) != {"op", "controller", "tokens"}
        or effect.get("op") != "create_token_batch"
        or effect.get("controller") != "$controller"
    ):
        return ()
    tokens = effect.get("tokens")
    if not isinstance(tokens, list) or not 2 <= len(tokens) <= 3:
        return ()
    closures: list[tuple[tuple[str, ...], set[str]]] = []
    for token in tokens:
        if not isinstance(token, Mapping):
            return ()
        closure = _fixed_token_effect_closure(
            {
                "op": "create_token",
                "controller": "$controller",
                **token,
            }
        )
        if closure is None:
            return ()
        closures.append(closure)
    expected_mechanics = {
        FIXED_TOKEN_DEFINITION_BATCH_MECHANIC,
        *(
            mechanic
            for _capabilities, mechanics in closures
            for mechanic in mechanics
        ),
    }
    if _token_specific_mechanics(mechanic_ids) != expected_mechanics:
        return ()
    return tuple(
        dict.fromkeys(
            capability
            for capabilities, _mechanics in closures
            for capability in capabilities
        )
    )


def entry_x_program_runtime_dependencies(program):
    from ..entry_x_amount_model import (
        ENTRY_X_AMOUNT_MECHANIC, entry_x_amount_runtime_dependencies,
        project_entry_x_token_batch, uses_entry_x_token_batch,
    )

    required = entry_x_amount_runtime_dependencies(program)
    if not required:
        return ()
    from .effect_shape_walkers import nested_effects

    batches = tuple(effect for effect in nested_effects(program.effects) if uses_entry_x_token_batch(effect))
    if not batches:
        return required
    if (
        len(batches) != 1 or tuple(program.effects) != batches or program.target_schema is not None
        or program.event_condition is not None or program.handlers or program.cost_schema is not None
    ):
        raise ValueError("An entry-X token batch requires its complete unqualified self-entry instruction")
    from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
    from ..compiler.declared_effect_amounts import DECLARED_EFFECT_AMOUNT_MECHANIC
    from ..query_effect_amount_model import DECLARED_AMOUNT_CAPABILITY

    mechanics = set(program.coverage) - {
        "generated_oracle_ir", "triggered_ability", CURRENT_ABILITY_FRAGMENT_COVERAGE,
        DECLARED_EFFECT_AMOUNT_MECHANIC, ENTRY_X_AMOUNT_MECHANIC,
    }
    owned = fixed_token_creation_node_capabilities(
        effects=(project_entry_x_token_batch(batches[0]),), target_schema=None, mechanic_ids=mechanics,
    )
    if "token.creation.fixed_definition" not in owned:
        raise ValueError("Entry-X token batch requires both fixed token definitions")
    return tuple(sorted({*required, *owned, DECLARED_AMOUNT_CAPABILITY}))


def fixed_token_creation_covered_mechanics(
    capability_ids: set[str],
) -> tuple[str, ...]:
    """Return mechanics owned by the closed token-production capabilities."""

    from .token_mill_repeat import TOKEN_MILL_REPEAT_CAPABILITY, TOKEN_MILL_REPEAT_MECHANIC

    covered: set[str] = set()
    if TOKEN_MILL_REPEAT_CAPABILITY in capability_ids:
        covered.add(TOKEN_MILL_REPEAT_MECHANIC)
    from .fixed_basic_land_token import FIXED_BASIC_LAND_TOKEN_CAPABILITY, FIXED_BASIC_LAND_TOKEN_MECHANIC

    if FIXED_BASIC_LAND_TOKEN_CAPABILITY in capability_ids:
        covered.add(FIXED_BASIC_LAND_TOKEN_MECHANIC)
    if 'token.creation.fixed_definition_choice' in capability_ids:
        covered.add('fixed-token-definition-choice')
    if 'token.creation.defined_transforming' in capability_ids:
        covered.add('cr-111-tokens')
    if "token.creation.fixed_definition" in capability_ids:
        covered.update(
            {
                "cr-111-tokens",
                "fixed-token-definition-batch",
                "fixed-token-production",
                "fixed-named-token",
            }
        )
    if {
        "token.creation.fixed_definition",
        "combat.declaration.typed_components",
    }.issubset(capability_ids):
        covered.add("fixed-token-declaration-fragment")
    if FIXED_DISTRIBUTED_TOKEN_CAPABILITY_ID in capability_ids:
        covered.add(FIXED_DISTRIBUTED_TOKEN_MECHANIC_ID)
    if INVESTIGATE_CAPABILITY_ID in capability_ids:
        covered.update(
            {
                INVESTIGATE_MECHANIC_ID,
                "fixed-token-production",
                "cr-111-tokens",
            }
        )
    if AFTERLIFE_CAPABILITY_ID in capability_ids:
        covered.update(
            {
                AFTERLIFE_MECHANIC_ID,
                "fixed-token-production",
                "cr-111-tokens",
                "cr-603-handling-triggered-abilities",
            }
        )
    if OCCURRENCE_COPY_RECIPIENT_CAPABILITY in capability_ids:
        covered.add(OCCURRENCE_COPY_RECIPIENT_MECHANIC)
    if TOKEN_COPY_BATCH_CAPABILITY in capability_ids:
        covered.add(TOKEN_COPY_BATCH_MECHANIC)
    if FIXED_TOKEN_COPY_CAPABILITY_ID in capability_ids:
        covered.update(
            {
                "cr-111-tokens",
                "cr-115-targets",
                "cr-707-copying-objects",
                "fixed-token-copy",
                "fixed-token-production",
            }
        )
    if FIXED_DELAYED_TOKEN_CAPABILITY_ID in capability_ids:
        covered.update(
            {
                "cr-111-tokens",
                "cr-603-handling-triggered-abilities",
                "fixed-delayed-token-creation",
                "fixed-named-token",
                "fixed-token-production",
            }
        )
    if 'token.creation.target_player_copy' in capability_ids:
        covered.add('target-player-copy-creation')
    return tuple(sorted(covered))


def token_creation_component_mechanics(effects, parent_mechanics):
    """Reconstruct only the independently validated token leaf's mechanics."""
    effect = effects[0] if len(effects) == 1 else {}
    characteristics = effect.get('characteristics')
    keywords = characteristics.get('keywords', ()) if isinstance(characteristics, Mapping) else ()
    mechanics = {'cr-111-tokens', *(str(keyword).casefold() for keyword in keywords if isinstance(keyword, str))}
    if effect.get("op") == SEQUENTIAL_INVESTIGATE_OPERATION:
        return mechanics | {INVESTIGATE_MECHANIC_ID, FIXED_TOKEN_PRODUCTION_MECHANIC_ID}
    if effect.get("op") == FIXED_DISTRIBUTED_TOKEN_OPERATION:
        try:
            scope, _dependencies, owned = _distributed_token_creation_closure(effect)
        except (TypeError, ValueError, KeyError):
            return set()
        return owned | ({"cr-115-targets"} if scope is FixedTokenCreatorScope.CONTROLLER_AND_TARGET_OPPONENT else set())
    if len(effects) == 1 and ('copy_spec' in effect or effect.get('op') == 'populate'):
        return parent_mechanics
    if "target" in effect and "cr-115-targets" in parent_mechanics:
        mechanics.add("cr-115-targets")
    if (effect.get('op') == 'create_token'
            and isinstance(characteristics, Mapping) and characteristics.get('ability_fragments')):
        closure = _fixed_token_effect_closure(effect)
        if closure is not None:
            # A validated definition supplies canonical names for numeric keywords.
            mechanics = set(closure[1]) - {TYPED_TOKEN_ABILITY_MECHANIC_ID}
            if "target" in effect and "cr-115-targets" in parent_mechanics:
                mechanics.add("cr-115-targets")
            if TYPED_TOKEN_ABILITY_MECHANIC_ID in parent_mechanics and TYPED_TOKEN_ABILITY_CAPABILITY_ID in closure[0]:
                mechanics.add(TYPED_TOKEN_ABILITY_MECHANIC_ID)
    try:
        if effect.get('op') == 'choose_fixed_attacking_token_destinations':
            from .attacking_token_results import attacking_token_result_closure, ATTACKING_TOKEN_MECHANIC
            _required, owned = attacking_token_result_closure(effect)
            mechanics.update(owned);mechanics.add(ATTACKING_TOKEN_MECHANIC)
        if 'token_cleanup' in effect:
            from ..created_token_cleanup_model import CreatedTokenCleanupSpec, TOKEN_CLEANUP_MECHANIC
            CreatedTokenCleanupSpec.from_dict(effect['token_cleanup'])
            mechanics.add(TOKEN_CLEANUP_MECHANIC)
    except (TypeError, ValueError, KeyError):
        return set()
    return mechanics


__all__ = [
    "fixed_token_creation_covered_mechanics",
    "fixed_token_creation_node_capabilities",
    "token_creation_component_mechanics",
]
