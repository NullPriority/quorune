from __future__ import annotations

"""Strict capability shape for fixed-definition token instructions."""

import re
from typing import Any, Mapping, Sequence

from ..ability_fragments import (
    AbilityFragmentError,
    AllCreatureTypesCharacteristicDefinitionSpec,
    canonical_ability_fragments,
)
from ..compiler.token_templates import (
    FIXED_TOKEN_DEFINITION_BATCH_MECHANIC,
    fixed_token_creation_effect_template,
)
from ..compiler.direct_target import direct_permanent_target_spec
from ..declaration_fragments import DeclarationRestrictionTemplate
from ..fixed_token_production import (
    AFTERLIFE_CAPABILITY_ID,
    AFTERLIFE_MECHANIC_ID,
    FIXED_DELAYED_TOKEN_CAPABILITY_ID,
    FIXED_TOKEN_COPY_CAPABILITY_ID,
    FIXED_TOKEN_PRODUCTION_MECHANIC_ID,
    FixedTokenProductionError,
    INVESTIGATE_CAPABILITY_ID,
    INVESTIGATE_MECHANIC_ID,
    afterlife_token_effect,
    clue_token_effect,
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
    }
)
_PREDEFINED_CAPABILITIES = {
    "Treasure": ("mana.activated.fixed_output",),
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
        "activated_ability",
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
        "triggered_ability",
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
    if name not in _PREDEFINED_CAPABILITIES:
        return None
    expected = fixed_token_creation_effect_template(
        f"Create a {name} token."
    )
    if expected is None:
        return None
    canonical = dict(expected.effect)
    supplied = dict(effect)
    supplied["quantity"] = 1
    supplied.pop("tapped", None)
    if supplied != canonical:
        return None
    characteristics = effect.get("characteristics")
    keywords = (
        characteristics.get("keywords", [])
        if isinstance(characteristics, Mapping)
        else []
    )
    return (
        (
            "token.creation.fixed_definition",
            *_PREDEFINED_CAPABILITIES[str(name)],
        ),
        {_TOKEN_MECHANIC, *(str(value).casefold() for value in keywords)},
    )


def _fixed_creature_effect_closure(
    effect: Mapping[str, Any],
) -> tuple[tuple[str, ...], set[str]] | None:
    characteristics = effect.get("characteristics")
    if not isinstance(characteristics, Mapping):
        return None
    allowed = {
        "type_line",
        "colors",
        "power",
        "toughness",
        "keywords",
        "ability_fragments",
    }
    required = {"type_line", "colors", "power", "toughness"}
    if not required.issubset(characteristics) or set(characteristics) - allowed:
        return None
    name = effect.get("name")
    if not isinstance(name, str) or re.fullmatch(
        r"[A-Z][A-Za-z']*(?:[ -][A-Z][A-Za-z']*)*", name
    ) is None:
        return None
    type_line = str(characteristics.get("type_line") or "")
    if re.fullmatch(
        r"Token (?:Legendary )?(?:Artifact )?Creature(?: Enchantment)? — "
        r"[A-Z][A-Za-z']*(?:[ -][A-Z][A-Za-z']*)*",
        type_line,
    ) is None:
        return None
    colors = characteristics.get("colors")
    if (
        not isinstance(colors, list)
        or len(colors) > 3
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
    capabilities = {"token.creation.fixed_definition"}
    mechanics = {
        _TOKEN_MECHANIC,
        *(keyword.casefold() for keyword in keywords),
    }
    subtype_text = type_line.split(" — ", 1)[1]
    if name != subtype_text or "Legendary" in type_line:
        mechanics.add("fixed-named-token")
    raw_fragments = characteristics.get("ability_fragments", [])
    if not isinstance(raw_fragments, list) or len(raw_fragments) > 1:
        return None
    try:
        fragments = canonical_ability_fragments(raw_fragments)
    except (AbilityFragmentError, TypeError, ValueError):
        return None
    if len(fragments) != len(raw_fragments):
        return None
    for fragment in fragments:
        if isinstance(fragment, AllCreatureTypesCharacteristicDefinitionSpec):
            if "Changeling" not in keywords:
                return None
            capabilities.add("continuous.characteristics.changeling")
        elif isinstance(fragment, DeclarationRestrictionTemplate):
            if fragment.template_id not in {
                "intrinsic-block-prohibition-v1",
                "intrinsic-unblockable-v1",
            }:
                return None
            capabilities.add("combat.declaration.typed_components")
            mechanics.add("fixed-token-declaration-fragment")
            mechanics.update(fragment.mechanics)
        else:
            return None
    if "Changeling" in keywords and not any(
        isinstance(fragment, AllCreatureTypesCharacteristicDefinitionSpec)
        for fragment in fragments
    ):
        return None
    return tuple(sorted(capabilities)), mechanics


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
    if predefined is not None:
        return predefined
    return _fixed_creature_effect_closure(effect)


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


def fixed_token_creation_node_capabilities(
    *,
    effects: Sequence[Mapping[str, Any]],
    target_schema: Mapping[str, Any] | None,
    mechanic_ids: set[str],
) -> tuple[str, ...]:
    """Recognize a compiler-owned fixed token creation instruction."""

    if len(effects) != 1:
        return ()
    effect = effects[0]
    mechanics = set(mechanic_ids)
    if effect.get("op") == "delayed_trigger":
        if target_schema is not None:
            return ()
        return _fixed_delayed_token_capabilities(effect, mechanics)
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
    if target_schema is not None:
        return ()
    if effect.get("op") == "create_token":
        quantity = effect.get("quantity")
        try:
            expected_investigate = (
                clue_token_effect(quantity) if type(quantity) is int else None
            )
            expected_afterlife = (
                afterlife_token_effect(quantity) if type(quantity) is int else None
            )
        except FixedTokenProductionError:
            expected_investigate = None
            expected_afterlife = None
        if (
            INVESTIGATE_MECHANIC_ID in mechanics
            and {
                _TOKEN_MECHANIC,
                FIXED_TOKEN_PRODUCTION_MECHANIC_ID,
            }.issubset(mechanics)
            and expected_investigate is not None
            and dict(effect) == expected_investigate
        ):
            return (INVESTIGATE_CAPABILITY_ID,)
        if (
            AFTERLIFE_MECHANIC_ID in mechanics
            and {
                _TOKEN_MECHANIC,
                FIXED_TOKEN_PRODUCTION_MECHANIC_ID,
                "cr-603-handling-triggered-abilities",
            }.issubset(mechanics)
            and expected_afterlife is not None
            and dict(effect) == expected_afterlife
        ):
            return (AFTERLIFE_CAPABILITY_ID,)
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


def fixed_token_creation_covered_mechanics(
    capability_ids: set[str],
) -> tuple[str, ...]:
    """Return mechanics owned by the closed token-production capabilities."""

    covered: set[str] = set()
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
    return tuple(sorted(covered))


__all__ = [
    "fixed_token_creation_covered_mechanics",
    "fixed_token_creation_node_capabilities",
]
