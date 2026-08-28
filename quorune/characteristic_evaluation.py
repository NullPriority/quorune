from __future__ import annotations

import copy
import re
from typing import Any, Callable, Mapping, Sequence

from .continuous_effects import (
    CharacteristicState,
    ContinuousEffect,
    ContinuousEffectDuration,
    ContinuousOperation,
    Layer,
    evaluate_continuous_effects,
)
from .continuous_effect_model import ContinuousEffectError
from .model import CardInstance
from .morph import MORPH_FACE_DOWN_ANNOTATION
from .keyword_counters import keyword_counter_abilities
from .util import unique_preserving_order
from .ability_fragments import (
    ability_fragment_to_dict,
    canonical_ability_fragments,
)
from .abilities import ActivatedAbility
from .characteristic_fragments import (
    AllCreatureTypesCharacteristicDefinitionSpec,
    ColorlessCharacteristicDefinitionSpec,
    PowerToughnessCalculation,
    QueryCharacteristicModifierSpec,
    CharacteristicQuantitySpec,
)
from .creature_subtypes import CREATURE_SUBTYPES


_CARD_TYPES = {
    "artifact",
    "battle",
    "creature",
    "enchantment",
    "instant",
    "kindred",
    "land",
    "planeswalker",
    "sorcery",
}
_SUPERTYPES = frozenset("basic legendary ongoing snow world".split())


def type_parts(type_line: str) -> tuple[set[str], set[str], set[str]]:
    """Parse the public type-line vocabulary used by characteristic layers."""

    normalized = type_line.replace("—", "-")
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
        words.intersection(_SUPERTYPES),
    )


def _numeric(value: Any) -> int | None:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def _annotation_terms(
    card: CardInstance,
) -> tuple[dict[str, Any], list[str], list[str]]:
    temporary = dict(card.annotations.get("until_end_of_turn") or {})
    overrides = dict(card.annotations.get("copy_overrides") or {})
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
    card: CardInstance, result: Mapping[str, Any]
) -> CharacteristicState:
    card_types, subtypes, supertypes = type_parts(
        str(result.get("type_line") or "")
    )
    return CharacteristicState(
        name=str(result.get("name") or card.printed_name),
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
        power=_numeric(result.get("power")),
        toughness=_numeric(result.get("toughness")),
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


def _all_creature_types_effect(
    card: CardInstance,
    *,
    enabled: bool,
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
        effect_id=f"{card.object_id}:all-creature-types-definition",
        source_id=card.object_id,
        layer=Layer.TYPE,
        sublayer="4",
        timestamp=0,
        operations=(
            ContinuousOperation(
                "add_types",
                sorted(CREATURE_SUBTYPES),
                field="subtypes",
            ),
        ),
        characteristic_defining=True,
        duration=ContinuousEffectDuration.ZONE_OBJECT,
    )


def _object_continuous_effects(
    card: CardInstance,
    overrides: Mapping[str, Any],
    added_types: Sequence[str],
    added_subtypes: Sequence[str],
    *,
    has_all_creature_types_definition: bool,
    has_colorless_definition: bool,
    ignore_face_down: bool = False,
) -> list[ContinuousEffect]:
    effects: list[ContinuousEffect] = []
    copy_effect = _copy_effect(card, overrides)
    if copy_effect is not None:
        effects.append(copy_effect)
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
    if has_colorless_definition:
        effects.append(
            ContinuousEffect(
                effect_id=f"{card.object_id}:colorless-definition",
                source_id=card.object_id,
                layer=Layer.COLOR,
                sublayer="5",
                timestamp=0,
                operations=(ContinuousOperation("remove_all_colors"),),
                characteristic_defining=True,
                duration=ContinuousEffectDuration.ZONE_OBJECT,
            )
        )
    all_creature_types = _all_creature_types_effect(
        card,
        enabled=has_all_creature_types_definition,
        face_down_values=face_down_values,
        ignore_face_down=ignore_face_down,
    )
    if all_creature_types is not None:
        effects.append(all_creature_types)
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
    return effects


def _query_characteristic_effects(
    card: CardInstance,
    state: CharacteristicState,
    effects: Sequence[ContinuousEffect],
    *,
    context: Mapping[str, Any],
    count_resolver: Callable[[CharacteristicQuantitySpec], int],
) -> list[ContinuousEffect]:
    """Materialize typed live quantities at their CR 613 layer boundary.

    The source fragment is selected after copy/type/color evaluation. Quantity
    resolution is supplied by the engine through the same layer-5 boundary, so
    a counted object's later-layer power, toughness, or abilities cannot recurse
    back into the quantity that is currently being evaluated.
    """

    if card.zone != "battlefield" or card.phased_out:
        return []
    through_color = evaluate_continuous_effects(
        copy.deepcopy(state),
        (effect for effect in effects if effect.layer <= Layer.COLOR),
        context=context,
    )
    fragments = canonical_ability_fragments(
        through_color.characteristics.get("ability_fragments", ())
    )
    materialized: list[ContinuousEffect] = []
    for index, fragment in enumerate(fragments):
        if not isinstance(fragment, QueryCharacteristicModifierSpec):
            continue
        count = count_resolver(fragment.quantity)
        if type(count) is not int or count < 0:
            raise ContinuousEffectError(
                "Characteristic quantity resolvers must return a nonnegative integer"
            )
        multiplier = (
            count
            if fragment.calculation
            is PowerToughnessCalculation.PER_MATCHING_OBJECT
            else int(count >= fragment.minimum_count)
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
            "activated_abilities": [
                (
                    ability.to_dict()
                    if isinstance(ability, ActivatedAbility)
                    else ActivatedAbility.from_dict(ability).to_dict()
                )
                for ability in values["activated_abilities"]
            ],
        }
    )
    for field in ("power", "toughness", "loyalty", "defense"):
        if values[field] is not None:
            result[field] = str(values[field])
    if render_type_line:
        left = [
            *_ordered_words(
                values["supertypes"], "Basic Legendary Snow World".split()
            ),
            *_ordered_words(
                values["card_types"],
                (
                    "Artifact Battle Creature Enchantment Instant Kindred Land "
                    "Planeswalker Sorcery"
                ).split(),
            ),
        ]
        right = [str(value).title() for value in values["subtypes"]]
        result["type_line"] = " ".join(left) + (
            f" — {' '.join(right)}" if right else ""
        )
    if card.annotations.get("bestowed") and card.attached_to:
        result["oracle_text"] = (
            "Enchant creature\nEnchanted creature gets +1/+1."
        )
    return result


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
    has_all_creature_types_definition = (
        _has_all_creature_types_characteristic_definition(
            result,
            overrides,
        )
    )
    layered = bool(
        overrides
        or added_types
        or added_subtypes
        or card.temporary_keywords
        or keyword_counter_abilities(card.counters)
        or card.annotations.get("granted_ability_fragments")
        or card.annotations.get("bestowed")
        or has_colorless_definition
        or has_all_creature_types_definition
        or (
            card.face_down
            and not ignore_face_down
            and card.annotations.get(MORPH_FACE_DOWN_ANNOTATION) is not None
        )
        or runtime_effects
        or query_count_resolver is not None
    )
    if not layered:
        result["executable_oracle_text"] = str(
            result.get(
                "executable_oracle_text",
                result.get("oracle_text") or "",
            )
            or ""
        )
        result["keywords"] = unique_preserving_order(
            list(result.get("keywords") or [])
        )
        result["ability_fragments"] = [
            ability_fragment_to_dict(value)
            for value in canonical_ability_fragments(
                result.get("ability_fragments", ())
            )
        ]
        result["activated_abilities"] = [
            ActivatedAbility.from_dict(value).to_dict()
            for value in result.get("activated_abilities", ())
        ]
        return result

    state = _base_characteristic_state(card, result)
    effects = _object_continuous_effects(
        card,
        overrides,
        added_types,
        added_subtypes,
        has_all_creature_types_definition=(
            has_all_creature_types_definition
        ),
        has_colorless_definition=has_colorless_definition,
        ignore_face_down=ignore_face_down,
    )
    effects.extend(runtime_effects)
    context = {
        "object_id": card.object_id,
        "logical_object_id": card.logical_object_id,
        "ref": card.ref,
        "owner": card.owner,
        "controller": card.controller,
        "zone": card.zone,
        "token": card.is_token,
        "tapped": card.tapped,
        "phased_out": card.phased_out,
        "known_to_actor": True,
    }
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
    return _render_characteristics(
        card,
        result,
        values,
        render_type_line=render_type_line,
    )
