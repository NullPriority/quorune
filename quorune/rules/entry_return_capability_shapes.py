from __future__ import annotations

"""Capability shape for fixed controller entry-return requirements."""

from typing import Any, Iterable, Mapping, Sequence

from ..compiler.fixed_entry_return_requirements import (
    FIXED_ENTRY_RETURN_CAPABILITY,
    FIXED_ENTRY_RETURN_MECHANIC,
)
from .entry_return_payment import ENTRY_RETURN_PAYMENT_VERSION, ENTRY_RETURN_PAYMENT_VERSION_FIELD
from ..object_predicate import ObjectQueryError, ObjectQuerySpec


def _choice_is_closed(effect: Mapping[str, Any]) -> bool:
    expected = {
        "op",
        "actor",
        "players",
        "zone",
        "predicate",
        "count",
        "then",
        "prompt",
        *(("exclude_ref",) if "exclude_ref" in effect else ()),
        *(
            ("require_full_count", "fallback_effects")
            if effect.get("require_full_count") is True
            else ()
        ),
    }
    if (
        set(effect) != expected
        or effect.get("op") != "choose_cards_apnap"
        or effect.get("actor") != "$controller"
        or effect.get("players") != ["$controller"]
        or effect.get("zone") != "battlefield"
        or effect.get("count") not in {1, 2, 3}
        or effect.get("then") != "return_owner_hand"
        or type(effect.get("prompt")) is not str
        or not str(effect.get("prompt")).strip()
        or (
            "exclude_ref" in effect
            and effect.get("exclude_ref") not in ("$source", "$source.zone_object")
        )
    ):
        return False
    if effect.get("require_full_count") is True and effect.get(
        "fallback_effects"
    ) != [
        {"op": "sacrifice_if_present", "card": "$source.zone_object"}
    ]:
        return False
    try:
        predicate = ObjectQuerySpec.from_dict(effect["predicate"])
    except (ObjectQueryError, TypeError):
        return False
    return bool(
        predicate.zones == ("battlefield",)
        and predicate.controller is None
        and predicate.owner is None
        and predicate.commander is None
        and predicate.known_to_actor is None
        and predicate.exclude_ref is None
        and not predicate.include_phased_out
    )


def _contains_closed_choice(effect: Mapping[str, Any]) -> bool:
    if _choice_is_closed(effect):
        return True
    checked = ENTRY_RETURN_PAYMENT_VERSION_FIELD in effect
    if checked and (
        type(effect[ENTRY_RETURN_PAYMENT_VERSION_FIELD]) is not int
        or effect[ENTRY_RETURN_PAYMENT_VERSION_FIELD] != ENTRY_RETURN_PAYMENT_VERSION
    ):
        return False
    if set(effect) != {
        "op",
        "player",
        "prompt",
        "options",
        "then_by_choice",
        *((ENTRY_RETURN_PAYMENT_VERSION_FIELD,) if checked else ()),
    } or effect.get("op") != "choose_option":
        return False
    branches = effect.get("then_by_choice")
    if (
        effect.get("player") != "$controller"
        or effect.get("prompt")
        != "Return the required permanent(s) or sacrifice this permanent?"
        or effect.get("options")
        != [
            {"id": "return", "label": "Return permanent(s)"},
            {"id": "sacrifice", "label": "Sacrifice this permanent"},
        ]
        or not isinstance(branches, Mapping)
        or set(branches) != {"return", "sacrifice"}
        or not isinstance(branches["return"], list)
        or len(branches["return"]) != 1
        or not isinstance(branches["return"][0], Mapping)
        or not _choice_is_closed(branches["return"][0])
        or (checked and (branches["return"][0].get("require_full_count") is not True
                         or type(branches["return"][0].get("count")) is not int))
        or branches["sacrifice"]
        != [
            {"op": "sacrifice_if_present", "card": "$source.zone_object"}
        ]
    ):
        return False
    return True


def checked_entry_return_payment_is_closed(effect: Mapping[str, Any]) -> bool:
    return ENTRY_RETURN_PAYMENT_VERSION_FIELD in effect and _contains_closed_choice(effect)


def fixed_entry_return_node_capabilities(
    *,
    effects: Sequence[Mapping[str, Any]],
    target_schema: Mapping[str, Any] | None,
    mechanic_ids: Iterable[str],
) -> tuple[str, ...]:
    """Recognize one typed source return or controller return choice."""

    mechanics = {str(value).casefold() for value in mechanic_ids}
    if (
        FIXED_ENTRY_RETURN_MECHANIC not in mechanics
        or target_schema is not None
        or len(effects) != 1
    ):
        return ()
    effect = effects[0]
    source_return = (
        set(effect) == {"op", "card"}
        and effect.get("op") == "bounce"
        and effect.get("card") == "$source.zone_object"
    )
    if not source_return and not _contains_closed_choice(effect):
        return ()
    return (FIXED_ENTRY_RETURN_CAPABILITY, *(('permanent.return.owner_hand',) if source_return else ()))


def checked_entry_return_runtime_dependencies(program) -> tuple[str, ...]:
    from ..replacement.immutable import thaw_value
    from .effect_shape_walkers import nested_effects

    if not any(ENTRY_RETURN_PAYMENT_VERSION_FIELD in effect for effect in nested_effects(program.effects)):
        return ()
    if (
        len(program.effects) != 1
        or not checked_entry_return_payment_is_closed(thaw_value(program.effects[0]))
        or program.target_schema is not None
        or program.event != "permanent.enter"
        or program.active_zone != "battlefield"
        or program.event_condition != {"field": "card", "op": "eq", "value": "$source.ref"}
    ):
        raise ValueError("Checked entry return payment requires its own battlefield entry event")
    return (FIXED_ENTRY_RETURN_CAPABILITY,)


__all__ = [
    "checked_entry_return_payment_is_closed",
    "checked_entry_return_runtime_dependencies",
    "fixed_entry_return_node_capabilities",
]
