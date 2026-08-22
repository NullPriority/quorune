from __future__ import annotations

"""Strict capability shape for fixed-definition token instructions."""

import re
from typing import Any, Mapping, Sequence

from ..compiler.token_templates import (
    FIXED_TOKEN_DEFINITION_BATCH_MECHANIC,
    fixed_token_creation_effect_template,
)


_TOKEN_MECHANIC = "cr-111-tokens"
_COLOR_ORDER = "WUBRG"
_CREATURE_KEYWORDS = frozenset(
    {
        "Deathtouch",
        "Defender",
        "Double Strike",
        "First Strike",
        "Flying",
        "Haste",
        "Hexproof",
        "Indestructible",
        "Lifelink",
        "Menace",
        "Reach",
        "Trample",
        "Vigilance",
    }
)
_PREDEFINED_CAPABILITIES = {
    "Treasure": ("mana.activated.fixed_output",),
    "Food": ("life.change.effect",),
    "Map": ("keyword_action.explore.single",),
    "Clue": ("zone.draw.library_to_hand",),
}
_AUXILIARY_MECHANICS = frozenset(
    {
        "activated_ability",
        "cr-601-casting-spells",
        "cr-603-handling-triggered-abilities",
        "exhaust",
        "fixed-typed-event-effect-trigger",
        "generated_oracle_ir",
        "spell_resolution",
        "triggered_ability",
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
) -> tuple[str, ...]:
    name = effect.get("name")
    if name not in _PREDEFINED_CAPABILITIES:
        return ()
    expected = fixed_token_creation_effect_template(
        f"Create a {name} token."
    )
    if expected is None:
        return ()
    canonical = dict(expected.effect)
    supplied = dict(effect)
    supplied["quantity"] = 1
    supplied.pop("tapped", None)
    if supplied != canonical:
        return ()
    return (
        "token.creation.fixed_definition",
        *_PREDEFINED_CAPABILITIES[str(name)],
    )


def _fixed_creature_effect_mechanics(
    effect: Mapping[str, Any],
) -> set[str] | None:
    characteristics = effect.get("characteristics")
    if not isinstance(characteristics, Mapping):
        return None
    allowed = {"type_line", "colors", "power", "toughness", "keywords"}
    required = {"type_line", "colors", "power", "toughness"}
    if not required.issubset(characteristics) or set(characteristics) - allowed:
        return None
    name = effect.get("name")
    if not isinstance(name, str) or re.fullmatch(
        r"[A-Z][A-Za-z']*(?:[ -][A-Z][A-Za-z']*)*", name
    ) is None:
        return None
    if re.fullmatch(
        rf"Token (?:Artifact )?Creature(?: Enchantment)? — {re.escape(name)}",
        str(characteristics.get("type_line") or ""),
    ) is None:
        return None
    colors = characteristics.get("colors")
    if (
        not isinstance(colors, list)
        or len(colors) > 2
        or any(color not in _COLOR_ORDER for color in colors)
        or colors != sorted(set(colors), key=_COLOR_ORDER.index)
    ):
        return None
    for field in ("power", "toughness"):
        value = characteristics.get(field)
        if not isinstance(value, str) or not value.isdigit():
            return None
    keywords = characteristics.get("keywords", [])
    if (
        not isinstance(keywords, list)
        or any(keyword not in _CREATURE_KEYWORDS for keyword in keywords)
        or len(keywords) != len(set(keywords))
    ):
        return None
    return {
        _TOKEN_MECHANIC,
        *(keyword.casefold() for keyword in keywords),
    }


def _fixed_token_effect_shape_is_closed(
    effect: Mapping[str, Any],
) -> bool:
    expected_fields = {
        "op",
        "controller",
        "name",
        "quantity",
        "characteristics",
    }
    if "tapped" in effect:
        expected_fields.add("tapped")
    if set(effect) != expected_fields:
        return False
    if (
        effect.get("op") != "create_token"
        or effect.get("controller") != "$controller"
    ):
        return False
    quantity = effect.get("quantity")
    if type(quantity) is not int or quantity <= 0:
        return False
    return "tapped" not in effect or effect.get("tapped") is True


def _fixed_token_effect_closure(
    effect: Mapping[str, Any],
) -> tuple[tuple[str, ...], set[str]] | None:
    if not _fixed_token_effect_shape_is_closed(effect):
        return None
    predefined = _fixed_predefined_effect_is_closed(effect)
    if predefined:
        return predefined, {_TOKEN_MECHANIC}
    mechanics = _fixed_creature_effect_mechanics(effect)
    if mechanics is None:
        return None
    return ("token.creation.fixed_definition",), mechanics


def fixed_token_creation_node_capabilities(
    *,
    effects: Sequence[Mapping[str, Any]],
    target_schema: Mapping[str, Any] | None,
    mechanic_ids: set[str],
) -> tuple[str, ...]:
    """Recognize a compiler-owned fixed token creation instruction."""

    if target_schema is not None or len(effects) != 1:
        return ()
    effect = effects[0]
    if effect.get("op") == "create_token":
        closure = _fixed_token_effect_closure(effect)
        if closure is None:
            return ()
        capabilities, expected_mechanics = closure
        if _token_specific_mechanics(mechanic_ids) == expected_mechanics:
            return capabilities
        return ()
    if (
        set(effect) != {"op", "controller", "tokens"}
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


__all__ = ["fixed_token_creation_node_capabilities"]
