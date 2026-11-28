from __future__ import annotations

import copy
import re
from typing import Any, Callable, Iterable, Mapping, Sequence

from .continuous_effects import (
    CharacteristicState,
    ContinuousEffect,
    ContinuousEffectDuration,
    ContinuousObjectIdentity,
    ContinuousOperation,
    Layer,
    evaluate_continuous_effects,
)
from .continuous_effect_model import (
    CONTINUOUS_DEPENDENCY_ORDER_FIELD, CONTINUOUS_DEPENDENCY_ORDER_VERSION, ContinuousEffectError,
)
from .model import CardInstance
from .morph import MORPH_FACE_DOWN_ANNOTATION
from .keyword_counters import keyword_counter_abilities
from .declaration_fragments import DeclarationRestrictionTemplate
from .util import unique_preserving_order
from .ability_fragments import (
    ability_fragment_from_dict,
    ability_fragment_to_dict,
    canonical_ability_fragments,
)
from .abilities import ActivatedAbility
from .defined_token_faces import DEFINED_TOKEN_FACES_FIELD, defined_token_current_characteristics
from .characteristic_fragments import (
    AllCreatureTypesCharacteristicDefinitionSpec,
    ColorlessCharacteristicDefinitionSpec,
    DEFINING_LIFE_SCOPES,
    PowerToughnessCalculation,
    CharacteristicQuantityScope,
    QueryCharacteristicModifierSpec,
    QueryPowerToughnessDefinitionSpec,
    CharacteristicQuantitySpec,
)
from .creature_subtypes import CREATURE_SUBTYPES
from .type_subtype_vocabulary import CARD_SUPERTYPES
from .all_colors import AllColorsCharacteristicDefinitionSpec,ALL_COLORS
from .basic_land_types import AllNonbasicLandTypesDefinitionSpec, NONBASIC_LAND_SUBTYPES
from .off_battlefield_creatures import OffBattlefieldSelfCreatureSpec


_CARD_TYPES = {
    "artifact",
    "battle",
    "creature",
    "dungeon",
    "enchantment",
    "instant",
    "kindred",
    "land",
    "planeswalker",
    "sorcery",
}


def type_parts(type_line: str) -> tuple[set[str], set[str], set[str]]:
    """Parse the public type-line vocabulary used by characteristic layers."""

    normalized = type_line.replace("—", "-").replace('’',"'")
    left, _, right = normalized.partition("-")
    word_pattern = r"[A-Za-z]+(?:[-'][A-Za-z]+)*"
    words = {
        word.casefold() for word in re.findall(word_pattern, left)
    }
    return (
        words.intersection(_CARD_TYPES),
        {
            "time lord" if word.casefold() == "timelord" else word.casefold()
            for word in re.findall(
                word_pattern,
                re.sub(
                    r"\bTime\s+Lord\b",
                    "TimeLord",
                    right,
                    flags=re.IGNORECASE,
                ),
            )
        },
        words.intersection(CARD_SUPERTYPES),
    )


def render_layered_type_line(values: Mapping[str, Any]) -> str:
    left = [
        *_ordered_words(values["supertypes"], "Basic Legendary Snow World".split()),
        *_ordered_words(values["card_types"],
                        "Artifact Battle Creature Enchantment Instant Kindred Land Planeswalker Sorcery".split()),
    ]
    right = [str(value).title() for value in values["subtypes"]]
    return " ".join(left) + (f" — {' '.join(right)}" if right else "")


def _numeric(value: Any) -> int | None:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def _printed_stat(value: Any) -> int | None:
    number=_numeric(value)
    if number is not None:return number
    text=str(value).replace(' ','')
    star=re.fullmatch(r'(?:(?P<left>[+-]?\d+)\+)?\*(?:(?P<sign>[+-])(?P<right>\d+))?',text)
    if star is None:return None
    return int(star['left'] or 0)+(int(star['right'] or 0)*(-1 if star['sign']=='-' else 1))


def _annotation_terms(
    card: CardInstance,
) -> tuple[dict[str, Any], list[str], list[str]]:
    temporary = dict(card.annotations.get("until_end_of_turn") or {})
    overrides = dict(card.annotations.get("copy_overrides") or {})
    from .rules.copied_flip_faces import current_flip_copy_values
    overrides = current_flip_copy_values(card, overrides)
    if DEFINED_TOKEN_FACES_FIELD in overrides:
        overrides = defined_token_current_characteristics(card, overrides)
    from .shared_type_doors import SHARED_TYPE_COPY_FIELD
    if SHARED_TYPE_COPY_FIELD in overrides:
        from .rules.room_copy_values import room_copy_characteristics
        overrides = room_copy_characteristics(card, overrides)
    added_types = [
        str(value).strip()
        for value in card.annotations.get("continuous_add_types", [])
        if str(value).strip()
    ] + [
        str(value).strip()
        for value in temporary.get("add_types", [])
        if str(value).strip()
    ]
    chosen_subtype = (
        [str(card.annotations["chosen_creature_type"])]
        if card.annotations.get("chosen_creature_type_adds_subtype")
        and card.annotations.get("chosen_creature_type")
        else []
    )
    added_subtypes = chosen_subtype + [
        str(value).strip()
        for value in card.annotations.get("continuous_add_subtypes", [])
        if str(value).strip()
    ] + [
        str(value).strip()
        for value in temporary.get("add_subtypes", [])
        if str(value).strip()
    ]
    return overrides, added_types, added_subtypes


def _base_characteristic_state(
    card: CardInstance, result: Mapping[str, Any], *, resolve_undefined_stats: bool = True,
) -> CharacteristicState:
    card_types, subtypes, supertypes = type_parts(
        str(result.get("type_line") or "")
    )
    return CharacteristicState(
        name=str(result.get("name", "") if card.shared_type_doors is not None else result.get("name") or card.printed_name),
        controller=card.controller,
        mana_cost=str(result.get("mana_cost") or ""),
        mana_value=float(result.get("mana_value") or 0),
        text=str(result.get("oracle_text") or ""),
        executable_text=str(
            result.get(
                "executable_oracle_text",
                result.get("oracle_text") or "",
            )
            or ""
        ),
        supertypes=set(supertypes),
        card_types=set(card_types),
        subtypes=set(subtypes),
        colors={str(value).upper() for value in result.get("colors", [])},
        abilities=[str(value) for value in result.get("keywords", [])],
        ability_fragments=list(
            canonical_ability_fragments(
                result.get("ability_fragments", ())
            )
        ),
        activated_abilities=[
            ActivatedAbility.from_dict(value)
            for value in result.get("activated_abilities", ())
        ],
        power=(_printed_stat if resolve_undefined_stats else _numeric)(result.get("power")),
        toughness=(_printed_stat if resolve_undefined_stats else _numeric)(result.get("toughness")),
        loyalty=_numeric(result.get("loyalty")),
        defense=_numeric(result.get("defense")),
    )


def _copy_effect(
    card: CardInstance,
    overrides: Mapping[str, Any],
) -> ContinuousEffect | None:
    copy_values: dict[str, Any] = {}
    field_map = {
        "name": "name",
        "mana_cost": "mana_cost",
        "mana_value": "mana_value",
        "oracle_text": "text",
        "display_text": "text",
        "executable_oracle_text": "executable_text",
        "power": "power",
        "toughness": "toughness",
        "loyalty": "loyalty",
        "defense": "defense",
        "colors": "colors",
        "keywords": "abilities",
        "ability_fragments": "ability_fragments",
        "activated_abilities": "activated_abilities",
    }
    numeric_fields = {"power", "toughness", "loyalty", "defense"}
    for source_field, target_field in field_map.items():
        if source_field not in overrides:
            continue
        value = copy.deepcopy(overrides[source_field])
        if target_field in numeric_fields:
            value = _numeric(value)
            if value is None:
                continue
        copy_values[target_field] = value
    if (
        "display_text" in overrides
        and "oracle_text" not in overrides
        and "executable_oracle_text" not in overrides
    ):
        # Display-only prose is still a copiable visible characteristic, but
        # it is not an executable Oracle program.  Supplying the empty value
        # explicitly also prevents the layer-1 compatibility rule from
        # mirroring copied display text into executable_text.
        copy_values["executable_text"] = ""
    if (
        "oracle_text" in overrides
        and "activated_abilities" not in overrides
    ):
        copy_values["activated_abilities"] = []
    if overrides.get("type_line") is not None:
        copied_types, copied_subtypes, copied_supertypes = type_parts(
            str(overrides["type_line"])
        )
        copy_values.update(
            {
                "card_types": sorted(copied_types),
                "subtypes": sorted(copied_subtypes),
                "supertypes": sorted(copied_supertypes),
            }
        )
    if not copy_values:
        return None
    return ContinuousEffect(
        effect_id=f"{card.object_id}:copy",
        source_id=card.object_id,
        layer=Layer.COPY,
        sublayer="1a",
        timestamp=0,
        operations=(ContinuousOperation("copy_values", copy_values),),
        duration=ContinuousEffectDuration.ZONE_OBJECT,
    )


def _has_colorless_characteristic_definition(
    base: Mapping[str, Any],
    overrides: Mapping[str, Any],
) -> bool:
    fragments = overrides.get(
        "ability_fragments",
        base.get("ability_fragments", ()),
    )
    return any(
        isinstance(fragment, ColorlessCharacteristicDefinitionSpec)
        for fragment in canonical_ability_fragments(fragments)
    )


def _has_all_colors_definition(base,overrides):
    return any(isinstance(fragment,AllColorsCharacteristicDefinitionSpec) for fragment in
        canonical_ability_fragments(overrides.get('ability_fragments',base.get('ability_fragments',()))))


def _has_all_creature_types_characteristic_definition(
    base: Mapping[str, Any],
    overrides: Mapping[str, Any],
) -> bool:
    fragments = overrides.get(
        "ability_fragments",
        base.get("ability_fragments", ()),
    )
    return any(
        isinstance(fragment, AllCreatureTypesCharacteristicDefinitionSpec)
        for fragment in canonical_ability_fragments(fragments)
    )


def _has_all_nonbasic_land_types_definition(base, overrides):
    return any(isinstance(fragment, AllNonbasicLandTypesDefinitionSpec) for fragment in
               canonical_ability_fragments(overrides.get("ability_fragments", base.get("ability_fragments", ()))))


def _subtype_definition_effect(
    card: CardInstance,
    *,
    enabled: bool,
    name: str,
    subtypes: Iterable[str],
    face_down_values: Any,
    ignore_face_down: bool,
) -> ContinuousEffect | None:
    if not enabled or (
        card.face_down
        and not ignore_face_down
        and face_down_values is not None
    ):
        return None
    return ContinuousEffect(
        effect_id=f"{card.object_id}:{name}",
        source_id=card.object_id,
        layer=Layer.TYPE,
        sublayer="4",
        timestamp=0,
        operations=(
            ContinuousOperation(
                "add_types",
                sorted(subtypes),
                field="subtypes",
            ),
        ),
        characteristic_defining=True,
        duration=ContinuousEffectDuration.ZONE_OBJECT,
    )


def _copy_layer_effects(card,overrides):
    from dataclasses import replace
    from .composition_characteristics import composition_copy_effect
    from .replacement.face_status_copy import COPY_HISTORY_FIELD, ScopedCopyValues

    copy_effect = _copy_effect(card,overrides)
    history = card.annotations.get(COPY_HISTORY_FIELD)
    if copy_effect is not None and history is not None:
        timestamp = ScopedCopyValues.from_dict(history).effective.timestamp
        copy_effect = replace(copy_effect,timestamp=timestamp)
    return [effect for effect in (copy_effect,composition_copy_effect(card)) if effect is not None]


def _suspect_ability_effect(card: CardInstance) -> ContinuousEffect:
    prohibition = DeclarationRestrictionTemplate(
        template_id="intrinsic-block-prohibition-v1", declarations=("block",), scope="self",
    )
    return ContinuousEffect(
        effect_id=f"{card.object_id}:suspected-abilities", source_id=card.object_id,
        layer=Layer.ABILITY, sublayer="6", timestamp=card.suspected_at,
        operations=(
            ContinuousOperation("add_ability", "Menace"),
            ContinuousOperation("add_ability_fragment", ability_fragment_to_dict(prohibition)),
        ),
        duration=ContinuousEffectDuration.ZONE_OBJECT,
    )


def _object_continuous_effects(
    card: CardInstance,
    overrides: Mapping[str, Any],
    added_types: Sequence[str],
    added_subtypes: Sequence[str],
    *,
    has_all_creature_types_definition: bool,
    has_all_nonbasic_land_types_definition: bool,
    has_colorless_definition: bool,
    has_all_colors_definition: bool,
    ignore_face_down: bool = False,
) -> list[ContinuousEffect]:
    effects = _copy_layer_effects(card,overrides)
    face_down_values = card.annotations.get(MORPH_FACE_DOWN_ANNOTATION)
    if card.face_down and not ignore_face_down and face_down_values is not None:
        effects.append(
            ContinuousEffect(
                effect_id=f"{card.object_id}:face-down",
                source_id=card.object_id,
                layer=Layer.COPY,
                sublayer="1b",
                timestamp=0,
                operations=(
                    ContinuousOperation("face_down", face_down_values),
                ),
                duration=ContinuousEffectDuration.ZONE_OBJECT,
            )
        )
    all_colors_active=has_all_colors_definition and not (card.face_down and not ignore_face_down and face_down_values is not None)
    if has_colorless_definition or all_colors_active:
        effects.append(
            ContinuousEffect(
                effect_id=f"{card.object_id}:"+('all-colors-definition' if all_colors_active else 'colorless-definition'),
                source_id=card.object_id,
                layer=Layer.COLOR,
                sublayer="5",
                timestamp=0,
                operations=(ContinuousOperation('set_colors',ALL_COLORS) if all_colors_active else ContinuousOperation("remove_all_colors"),),
                characteristic_defining=True,
                duration=ContinuousEffectDuration.ZONE_OBJECT,
            )
        )
    for enabled, name, subtypes in (
        (has_all_creature_types_definition, "all-creature-types-definition", CREATURE_SUBTYPES),
        (has_all_nonbasic_land_types_definition, "all-nonbasic-land-types-definition", NONBASIC_LAND_SUBTYPES),
    ):
        if effect := _subtype_definition_effect(
            card, enabled=enabled, name=name, subtypes=subtypes,
            face_down_values=face_down_values, ignore_face_down=ignore_face_down,
        ):
            effects.append(effect)
    type_operations: list[ContinuousOperation] = []
    if card.annotations.get("bestowed") and card.attached_to:
        type_operations.extend(
            (
                ContinuousOperation(
                    "set_types", ["Enchantment"], field="card_types"
                ),
                ContinuousOperation(
                    "set_types", ["Aura"], field="subtypes"
                ),
            )
        )
    if added_types:
        type_operations.append(
            ContinuousOperation("add_types", added_types, field="card_types")
        )
    if added_subtypes:
        type_operations.append(
            ContinuousOperation(
                "add_types", added_subtypes, field="subtypes"
            )
        )
    if type_operations:
        effects.append(
            ContinuousEffect(
                effect_id=f"{card.object_id}:types",
                source_id=card.object_id,
                layer=Layer.TYPE,
                sublayer="4",
                timestamp=len(effects),
                operations=tuple(type_operations),
                duration=ContinuousEffectDuration.ZONE_OBJECT,
            )
        )
    if card.temporary_keywords:
        effects.append(
            ContinuousEffect(
                effect_id=f"{card.object_id}:keywords",
                source_id=card.object_id,
                layer=Layer.ABILITY,
                sublayer="6",
                timestamp=len(effects),
                operations=tuple(
                    ContinuousOperation("add_ability", keyword)
                    for keyword in card.temporary_keywords
                ),
                duration=ContinuousEffectDuration.UNTIL_END_OF_TURN,
            )
        )
    counter_abilities = keyword_counter_abilities(card.counters)
    if counter_abilities:
        effects.append(
            ContinuousEffect(
                effect_id=f"{card.object_id}:keyword-counters",
                source_id=card.object_id,
                layer=Layer.ABILITY,
                sublayer="6",
                timestamp=card.zone_timestamp,
                operations=tuple(
                    ContinuousOperation("add_ability", keyword)
                    for keyword in counter_abilities
                ),
                duration=ContinuousEffectDuration.ZONE_OBJECT,
            )
        )
    raw_granted_fragments = card.annotations.get(
        "granted_ability_fragments", ()
    )
    if raw_granted_fragments:
        try:
            granted_fragments = canonical_ability_fragments(
                raw_granted_fragments
            )
        except (TypeError, ValueError) as exc:
            raise ContinuousEffectError(
                "granted ability fragments are malformed"
            ) from exc
        effects.append(
            ContinuousEffect(
                effect_id=f"{card.object_id}:granted-ability-fragments",
                source_id=card.object_id,
                layer=Layer.ABILITY,
                sublayer="6",
                timestamp=card.zone_timestamp,
                operations=tuple(
                    ContinuousOperation(
                        "add_ability_fragment",
                        ability_fragment_to_dict(fragment),
                    )
                    for fragment in granted_fragments
                ),
                duration=ContinuousEffectDuration.ZONE_OBJECT,
            )
        )
    if card.suspected_at is not None:
        effects.append(_suspect_ability_effect(card))
    return effects


def _has_query_characteristic_input(
    state: CharacteristicState, effects: Sequence[ContinuousEffect],
) -> bool:
    """Check whether the ability-layer pass could discover a quantity fragment."""
    quantity_fragments = (QueryCharacteristicModifierSpec, QueryPowerToughnessDefinitionSpec)
    if any(isinstance(fragment, quantity_fragments) for fragment in state.ability_fragments):
        return True
    for effect in effects:
        for operation in effect.operations:
            # Copying, turning face down and granting a fragment are the
            # layer operations that can introduce new typed ability fragments.
            if operation.op == "add_ability_fragment":
                inputs = (operation.value,)
            elif operation.op in {"copy_values", "face_down"}:
                inputs = operation.value.get("ability_fragments", ())
            else:
                continue
            if any(isinstance(ability_fragment_from_dict(value), quantity_fragments) for value in inputs):
                return True
    return False


def _intrinsic_quantity_fragments_after_abilities(state,effects,*,context):
    """Separate source ability applicability from the quantity's layer-5 reads."""
    through_abilities=evaluate_continuous_effects(copy.deepcopy(state),
        (effect for effect in effects if effect.layer<=Layer.ABILITY),context=context)
    return canonical_ability_fragments(through_abilities.characteristics.get('ability_fragments',()))


def _query_characteristic_effects(
    card: CardInstance,
    state: CharacteristicState,
    effects: Sequence[ContinuousEffect],
    *,
    context: Mapping[str, Any],
    count_resolver: Callable[[CharacteristicQuantitySpec], int],
    maximum_layer: Layer | None = None,
    active_player: str | None = None,
) -> list[ContinuousEffect]:
    """Materialize typed live quantities at their CR 613 layer boundary.

    The source fragment is selected after layer-6 ability applicability. Quantity
    resolution uses the quantity's versioned layer boundary. Keyword-layer
    counts are deferred until their later modifier layer and cannot recurse
    through an earlier view of that modifier.
    """

    if card.zone == "battlefield" and card.phased_out:
        return []
    if not _has_query_characteristic_input(state, effects):
        return []
    fragments = _intrinsic_quantity_fragments_after_abilities(state,effects,context=context)
    materialized: list[ContinuousEffect] = []
    for index, fragment in enumerate(fragments):
        if isinstance(fragment, QueryPowerToughnessDefinitionSpec):
            if (fragment.quantity.scope is CharacteristicQuantityScope.OPPONENT_CHOSEN_COLOR_PERMANENTS
                    and maximum_layer is not None and maximum_layer < Layer.POWER_TOUGHNESS):
                continue
            count = count_resolver(fragment.quantity)
            if type(count) is not int or (count < 0 and fragment.quantity.scope not in DEFINING_LIFE_SCOPES):
                raise ContinuousEffectError(
                    "A characteristic definition requires a permitted integer quantity"
                )
            operations = tuple(
                ContinuousOperation(operation, count + adjustment)
                for enabled, operation, adjustment in (
                    (fragment.define_power, "set_power", fragment.power_adjustment),
                    (fragment.define_toughness, "set_toughness", fragment.toughness_adjustment),
                )
                if enabled
            )
            materialized.append(
                ContinuousEffect(
                    effect_id=(
                        f"{card.object_id}:query-characteristic-definition:{index}"
                    ),
                    source_id=card.object_id,
                    layer=Layer.POWER_TOUGHNESS,
                    sublayer="7a",
                    timestamp=card.zone_timestamp,
                    operations=operations,
                    characteristic_defining=True,
                    duration=ContinuousEffectDuration.ZONE_OBJECT,
                )
            )
            continue
        if (
            not isinstance(fragment, QueryCharacteristicModifierSpec)
            or card.zone != "battlefield"
        ):
            continue
        if maximum_layer is not None and maximum_layer < Layer.POWER_TOUGHNESS and (
            fragment.controller_turn_only or fragment.quantity.schema_version == 6
            or fragment.quantity.scope is CharacteristicQuantityScope.ATTACKING_DEFENDER_ZONE
        ):
            continue
        if fragment.controller_turn_only and card.controller != active_player:
            continue
        count = count_resolver(fragment.quantity)
        if type(count) is not int or count < 0:
            raise ContinuousEffectError(
                "Characteristic quantity resolvers must return a nonnegative integer"
            )
        if count < fragment.minimum_count:
            continue
        multiplier = (
            count
            if fragment.calculation
            is PowerToughnessCalculation.PER_MATCHING_OBJECT
            else 1
        )
        if not multiplier:
            continue
        prefix = f"{card.object_id}:query-characteristic:{index}"
        if fragment.add_abilities:
            materialized.append(
                ContinuousEffect(
                    effect_id=f"{prefix}:abilities",
                    source_id=card.object_id,
                    layer=Layer.ABILITY,
                    sublayer="6",
                    timestamp=card.zone_timestamp,
                    operations=tuple(
                        ContinuousOperation("add_ability", ability)
                        for ability in fragment.add_abilities
                    ),
                    duration=ContinuousEffectDuration.ZONE_OBJECT,
                )
            )
        if fragment.power or fragment.toughness:
            materialized.append(
                ContinuousEffect(
                    effect_id=f"{prefix}:power-toughness",
                    source_id=card.object_id,
                    layer=Layer.POWER_TOUGHNESS,
                    sublayer="7c",
                    timestamp=card.zone_timestamp,
                    operations=(
                        ContinuousOperation(
                            "modify_power_toughness",
                            [
                                fragment.power * multiplier,
                                fragment.toughness * multiplier,
                            ],
                        ),
                    ),
                    duration=ContinuousEffectDuration.ZONE_OBJECT,
                )
            )
    return materialized


def _ordered_words(
    values_to_order: Sequence[str], preferred: Sequence[str]
) -> list[str]:
    by_lower = {
        str(value).casefold(): str(value).title()
        for value in values_to_order
    }
    preferred_lower = {value.casefold() for value in preferred}
    return [
        value for value in preferred if value.casefold() in by_lower
    ] + [
        by_lower[key]
        for key in sorted(by_lower)
        if key not in preferred_lower
    ]


def _render_characteristics(
    card: CardInstance,
    result: dict[str, Any],
    values: Mapping[str, Any],
    *,
    render_type_line: bool,
) -> dict[str, Any]:
    result.update(
        {
            "name": values["name"],
            "mana_cost": values["mana_cost"],
            "mana_value": values["mana_value"],
            "oracle_text": values["text"],
            "executable_oracle_text": values["executable_text"],
            "colors": [
                color for color in "WUBRGC" if color in set(values["colors"])
            ],
            "keywords": unique_preserving_order(values["abilities"]),
            "ability_fragments": [
                ability_fragment_to_dict(value)
                for value in canonical_ability_fragments(
                    values["ability_fragments"]
                )
            ],
            # The layer evaluator supplies a fresh, already validated snapshot.
            "activated_abilities": values["activated_abilities"],
        }
    )
    for field in ("power", "toughness", "loyalty", "defense"):
        if values[field] is not None:
            result[field] = str(values[field])
    if values.get('prohibited_keywords'):
        result['prohibited_keywords'] = list(values['prohibited_keywords'])
    else:
        result.pop('prohibited_keywords', None)
    if render_type_line:
        result["type_line"] = render_layered_type_line(values)
    if card.annotations.get("bestowed") and card.attached_to:
        result["oracle_text"] = (
            "Enchant creature\nEnchanted creature gets +1/+1."
        )
    return result


def _normalized_unlayered_characteristics(result):
    """Normalize an isolated base snapshot when no layer evaluation is needed."""
    result["executable_oracle_text"] = str(
        result.get("executable_oracle_text",result.get("oracle_text") or "") or ""
    )
    result["keywords"] = unique_preserving_order(list(result.get("keywords") or []))
    result["ability_fragments"] = [ability_fragment_to_dict(value)
        for value in canonical_ability_fragments(result.get("ability_fragments",()))]
    result["activated_abilities"] = [ActivatedAbility.from_dict(value).to_dict()
        for value in result.get("activated_abilities",())]
    return result


def _card_characteristic_query_context(card, *, enchanted=False, equipped=False, modified=False, ignore_face_down=False):
    return {
        "object_id": card.object_id,
        "logical_object_id": card.logical_object_id,
        "ref": card.ref,
        "owner": card.owner,
        "controller": card.controller,
        "zone": card.zone,
        "token": card.is_token,
        "commander": card.is_commander,
        "tapped": card.tapped,
        "face_down": card.face_down and not ignore_face_down,
        "phased_out": card.phased_out,
        "known_to_actor": True,
        "counters": card.counters,
        "attacking": card.attacking is not None,
        "blocking": card.blocking is not None,
        "enchanted": enchanted,
        "equipped": equipped,
        "modified": modified,
        "monstrous": card.monstrous_value is not None,
    }


def battlefield_type_dependency_view(
    card, base, *, runtime_effects=(), ignore_face_down=False, linked_exile_resolver=None,
    dependency_order_version=CONTINUOUS_DEPENDENCY_ORDER_VERSION,
):
    """Prepare the real pre-type state and per-object operations without mutating the game."""
    overrides, added_types, added_subtypes = _annotation_terms(card)
    state = _base_characteristic_state(card, base, resolve_undefined_stats=False)
    object_effects = _object_continuous_effects(
        card, overrides, added_types, added_subtypes,
        has_all_creature_types_definition=_has_all_creature_types_characteristic_definition(base, overrides),
        has_all_nonbasic_land_types_definition=_has_all_nonbasic_land_types_definition(base, overrides),
        has_colorless_definition=False, has_all_colors_definition=False, ignore_face_down=ignore_face_down,
    )
    effects = (*object_effects, *runtime_effects)
    context = _card_characteristic_query_context(card, ignore_face_down=ignore_face_down)
    context[CONTINUOUS_DEPENDENCY_ORDER_FIELD] = dependency_order_version
    if linked_exile_resolver is not None:
        context["linked_exile_characteristics"] = linked_exile_resolver
    evaluate_continuous_effects(state, (effect for effect in effects if effect.layer < Layer.TYPE),
                                context=context)
    return state, context, tuple(effect for effect in effects if effect.layer is Layer.TYPE)



def off_battlefield_self_creature_effects(
    card: CardInstance, fragments: Sequence[Any], *, ignore_face_down: bool = False,
) -> tuple[ContinuousEffect, ...]:
    if card.zone == "battlefield" or (card.face_down and not ignore_face_down):
        return ()
    specs = tuple(fragment for fragment in canonical_ability_fragments(fragments)
                  if isinstance(fragment, OffBattlefieldSelfCreatureSpec))
    if not specs:
        return ()
    source = ContinuousObjectIdentity(card.object_id, card.logical_object_id)
    return tuple(effect for index, spec in enumerate(specs)
                 for effect in spec.layer_effects(source, timestamp=card.zone_timestamp, occurrence=index))


def evaluate_card_characteristics(
    card: CardInstance,
    base: Mapping[str, Any],
    *,
    runtime_effects: Sequence[ContinuousEffect] = (),
    ignore_face_down: bool = False,
    query_count_resolver: Callable[
        [CharacteristicQuantitySpec], int
    ]
    | None = None,
    maximum_layer: Layer | None = None,
    enchanted: bool = False,
    equipped: bool = False,
    modified: bool = False,
    active_player: str | None = None,
    type_order=None,
    linked_exile_resolver=None,
    dependency_order_version: int | None = CONTINUOUS_DEPENDENCY_ORDER_VERSION,
) -> dict[str, Any]:
    """Evaluate one object's declarative CR 613 characteristic state.

    The owner is independent of CommanderEngine so authoritative rules and
    permission-aware projections can render the same committed result. Legacy
    annotation-backed records remain readable while new resolution effects use
    the immutable continuous-effect journal.
    """

    result = copy.deepcopy(dict(base))
    overrides, added_types, added_subtypes = _annotation_terms(card)
    has_colorless_definition = _has_colorless_characteristic_definition(
        result,
        overrides,
    )
    has_all_colors_definition = _has_all_colors_definition(result,overrides)
    has_all_land_types = _has_all_nonbasic_land_types_definition(result, overrides)
    has_all_creature_types_definition = (
        _has_all_creature_types_characteristic_definition(
            result,
            overrides,
        )
    )
    source_zone_effects = () if maximum_layer is not None and maximum_layer < Layer.TYPE else (
        off_battlefield_self_creature_effects(
            card, overrides.get("ability_fragments", result.get("ability_fragments", ())),
            ignore_face_down=ignore_face_down,
        )
    )
    layered = bool(
        overrides
        or card.permanent_composition is not None
        or added_types
        or added_subtypes
        or card.temporary_keywords
        or keyword_counter_abilities(card.counters)
        or card.annotations.get("granted_ability_fragments")
        or card.annotations.get("bestowed")
        or has_colorless_definition
        or has_all_colors_definition
        or has_all_creature_types_definition
        or has_all_land_types
        or (
            card.face_down
            and not ignore_face_down
            and card.annotations.get(MORPH_FACE_DOWN_ANNOTATION) is not None
        )
        or runtime_effects
        or source_zone_effects
        or query_count_resolver is not None
    )
    if not layered:
        return _normalized_unlayered_characteristics(result)

    state = _base_characteristic_state(card,result,resolve_undefined_stats=(
        maximum_layer is None or maximum_layer>=Layer.POWER_TOUGHNESS))
    effects = _object_continuous_effects(
        card,
        overrides,
        added_types,
        added_subtypes,
        has_all_creature_types_definition=(
            has_all_creature_types_definition
        ),
        has_colorless_definition=has_colorless_definition,
        has_all_colors_definition=has_all_colors_definition,
        has_all_nonbasic_land_types_definition=has_all_land_types,
        ignore_face_down=ignore_face_down,
    )
    effects.extend((*source_zone_effects, *runtime_effects))
    context = _card_characteristic_query_context(
        card, enchanted=enchanted, equipped=equipped, modified=modified, ignore_face_down=ignore_face_down,
    )
    context[CONTINUOUS_DEPENDENCY_ORDER_FIELD] = dependency_order_version
    if linked_exile_resolver is not None:
        context["linked_exile_characteristics"] = linked_exile_resolver
    if type_order is not None:
        context["type_order"] = type_order
    if query_count_resolver is not None and (
        maximum_layer is None or maximum_layer >= Layer.ABILITY
    ):
        effects.extend(
            _query_characteristic_effects(
                card,
                state,
                effects,
                context=context,
                count_resolver=query_count_resolver,
                maximum_layer=maximum_layer,
                active_player=active_player,
            )
        )
    if maximum_layer is not None:
        effects = [
            effect for effect in effects if effect.layer <= maximum_layer
        ]
    evaluated = evaluate_continuous_effects(
        state,
        effects,
        context=context,
    )
    values = evaluated.characteristics
    applied = set(evaluated.applied_effects)
    render_type_line = any(
        effect.effect_id in applied
        and (
            effect.layer is Layer.TYPE
            or any(
                operation.op == "face_down"
                or (
                    operation.op == "copy_values"
                    and bool(
                        {"supertypes", "card_types", "subtypes"}.intersection(
                            operation.value
                        )
                    )
                )
                for operation in effect.operations
            )
        )
        for effect in effects
    )
    characteristics = _render_characteristics(
        card,
        result,
        values,
        render_type_line=render_type_line,
    )
    if "saga" in values["subtypes"] and any(
        effect.effect_id in applied
        and any(operation.op in {"set_basic_land_types", "remove_all_abilities"} for operation in effect.operations)
        for effect in effects
    ):
        from .ability_fragment_primitives import INTRINSIC_ABILITIES_REMOVED

        characteristics[INTRINSIC_ABILITIES_REMOVED] = True
    return characteristics
