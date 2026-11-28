from __future__ import annotations

"""Substitute definitions of matching members of a prepared token creation event."""

from collections.abc import Mapping, Sequence
from typing import Any

from .characteristic_evaluation import type_parts
from .fixed_token_production import predefined_token_identity
from .replacement.immutable import thaw_value
from .standard_token_abilities import (
    StandardTokenAbilityProfile, TOKEN_ABILITY_PROFILE_FIELD, standard_token_characteristics,
)


CREATURE_TOKEN_DEFINITION_HANDLER = "replacement.token.creature-definition.v1"
CREATURE_TOKEN_DEFINITION_CAPABILITY = "token.creation.replace_creature_definition"
CREATURE_TOKEN_TRIPLING_HANDLER = "replacement.token.creature-quantity.v1"
CREATURE_TOKEN_TRIPLING_CAPABILITY = "token.creation.creature_quantity_replacement"
CREATION_TYPE_LINE = "_creation_type_line"
PREDEFINED_TOKEN_SET_HANDLER = "replacement.token.predefined-artifact-set.v1"
PREDEFINED_TOKEN_SET_CAPABILITY = "token.creation.replace_predefined_artifact_set"
_PREDEFINED_TOKEN_SET = {
    "Clue": StandardTokenAbilityProfile.TWO_SAC_DRAW_CARD,
    "Food": StandardTokenAbilityProfile.TWO_TAP_SAC_GAIN_THREE_LIFE,
    "Treasure": StandardTokenAbilityProfile.TAP_SAC_ANY_COLOR_MANA,
}
PREDEFINED_TOKEN_SET_SUBTYPES = tuple(name.casefold() for name in _PREDEFINED_TOKEN_SET)

_SUPERSEDED_DEFINITION_FIELDS = {
    "name", "characteristics", "copy_of", "copy_source_zone", "copy_snapshot",
    "aura_target_ref", "battle_protector", CREATION_TYPE_LINE,
}
_NONCREATURE_REPLACED_FIELDS = _SUPERSEDED_DEFINITION_FIELDS | {"attacking", "_attacking_group"}


def _creation_type_line(spec: Mapping[str, Any]) -> str:
    if CREATION_TYPE_LINE in spec:
        value = spec[CREATION_TYPE_LINE]
    elif spec.get("copy_of") is not None:
        raise ValueError("Copied creation is missing its captured type line")
    else:
        characteristics = spec.get("characteristics")
        if not isinstance(characteristics, Mapping):
            raise ValueError("Token creation characteristics must be an object")
        value = characteristics.get("type_line")
    if type(value) is not str:
        raise ValueError("Token creation requires a captured type line")
    return value


def creation_member_is_creature(spec: Mapping[str, Any]) -> bool:
    return "creature" in type_parts(_creation_type_line(spec))[0]


def replace_creature_token_definitions(
    specs: Sequence[Mapping[str, Any]],
    *,
    name: str,
    characteristics: Mapping[str, Any],
    handler_id: str,
    source_ref: str,
) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    """Apply a fixed creature definition and return the new event type union."""

    if not isinstance(specs, (list, tuple)) or not specs:
        raise ValueError("Definition replacement requires a nonempty creation event")
    replaced_types, replaced_subtypes, _ = type_parts(_creation_type_line({"characteristics": characteristics}))
    if "creature" not in replaced_types:
        raise ValueError("A creature token must be replaced with a creature definition")
    result = []
    all_types: set[str] = set()
    all_subtypes: set[str] = set()
    replaced = False
    for raw in specs:
        if not isinstance(raw, Mapping):
            raise ValueError("Token creation members must be objects")
        spec = thaw_value(raw)
        quantity = spec.get("quantity")
        if type(quantity) is not int or quantity < 1:
            raise ValueError("Token definition replacement requires a positive current quantity")
        types, subtypes, _ = type_parts(_creation_type_line(spec))
        if "creature" in types:
            spec = {key: value for key, value in spec.items() if key not in _SUPERSEDED_DEFINITION_FIELDS}
            spec.update(
                name=name, characteristics=thaw_value(characteristics),
                replacement_component={"handler_id": handler_id, "source": source_ref, "quantity": quantity},
            )
            types, subtypes = replaced_types, replaced_subtypes
            replaced = True
        all_types.update(types)
        all_subtypes.update(subtypes)
        result.append(spec)
    if not replaced:
        raise ValueError("Token definition replacement requires an actual creature member")
    return result, sorted(all_types), sorted(all_subtypes)


def replace_predefined_artifact_token_set(
    specs: Sequence[Mapping[str, Any]], *, handler_id: str, source_ref: str,
) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    """Produce each predefined artifact for every matching current token."""
    if not isinstance(specs, (list, tuple)) or not specs:
        raise ValueError("Predefined token substitution requires a nonempty creation event")
    definitions = []
    for subtype, profile in _PREDEFINED_TOKEN_SET.items():
        name, authored = predefined_token_identity(subtype, {
            "type_line": f"Token Artifact — {subtype}", "colors": [], "keywords": [],
            TOKEN_ABILITY_PROFILE_FIELD: profile.value,
        })
        characteristics = standard_token_characteristics(authored)
        characteristics["display_text"] = characteristics["activated_abilities"][0]["oracle_line"]
        definitions.append((name, characteristics))

    result = []
    all_types: set[str] = set()
    all_subtypes: set[str] = set()
    replaced = False
    for raw in specs:
        if not isinstance(raw, Mapping):
            raise ValueError("Token creation members must be objects")
        quantity = raw.get("quantity")
        if type(quantity) is not int or quantity < 1:
            raise ValueError("Predefined token substitution requires a positive current quantity")
        types, subtypes, _ = type_parts(_creation_type_line(raw))
        spec = thaw_value(raw)
        if subtypes.isdisjoint(PREDEFINED_TOKEN_SET_SUBTYPES):
            result.append(spec)
            all_types.update(types)
            all_subtypes.update(subtypes)
            continue
        # The three ordinary artifacts enter as noncreatures in this supported entry scope.
        status = {key: value for key, value in spec.items() if key not in _NONCREATURE_REPLACED_FIELDS}
        for name, characteristics in definitions:
            result.append({
                **status, "name": name, "characteristics": characteristics,
                "replacement_component": {
                    "handler_id": handler_id, "source": source_ref, "quantity": quantity,
                },
            })
        replaced = True
    if not replaced:
        raise ValueError("Predefined token substitution requires a matching token")
    all_types.add("artifact")
    all_subtypes.update(PREDEFINED_TOKEN_SET_SUBTYPES)
    return result, sorted(all_types), sorted(all_subtypes)
