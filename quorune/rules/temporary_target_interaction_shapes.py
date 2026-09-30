from __future__ import annotations

"""Capability shape for temporary direct-target interaction programs."""

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from ..ability_fragments import ProtectionSpec, ability_fragment_from_dict
from ..compiler.direct_target import DirectPermanentTargetSpec
from ..compiler.temporary_target_interactions import (
    TEMPORARY_TARGET_INTERACTION_MECHANIC,
)
from ..compiler.fixed_target_effect_sequences import TEMPORARY_TARGET_CHARACTERISTIC_KEYWORDS
from ..rules.permanent_predicate_capability_shapes import (
    direct_permanent_target_schema_is_closed,
    direct_target_predicate_capabilities,
)
from .temporary_target_interactions import (
    TEMPORARY_TARGET_INTERACTION_CAPABILITY,
    TargetConditionSpec,
    TemporaryTargetInteractionError,
)


_KEYWORDS = frozenset(value.title() for value in TEMPORARY_TARGET_CHARACTERISTIC_KEYWORDS) | {"Protection"}


def _condition(value: object) -> bool:
    if value is None:
        return True
    if not isinstance(value, Mapping):
        return False
    try:
        TargetConditionSpec.from_dict(value)
    except (TemporaryTargetInteractionError, TypeError, ValueError):
        return False
    return True


def _protection_fragment(value: object) -> bool:
    if not isinstance(value, Mapping):
        return False
    try:
        fragment = ability_fragment_from_dict(value)
        return (
            isinstance(fragment, ProtectionSpec) and fragment.schema_version == 1
            and (fragment.quality_kind.value == "color" or
                 (fragment.quality_kind.value == "card_type" and fragment.quality == "artifact"))
        )
    except (TypeError, ValueError):
        return False


def temporary_target_interaction_node_capabilities(
    *,
    effects: Sequence[Mapping[str, Any]],
    target_schema: Mapping[str, Any] | None,
    mechanic_ids: Iterable[str],
) -> tuple[str, ...]:
    """Recognize one closed temporary program over one direct target."""

    mechanics = {str(value).casefold() for value in mechanic_ids}
    if (
        TEMPORARY_TARGET_INTERACTION_MECHANIC not in mechanics
        or not 1 <= len(effects) <= 4
        or not direct_permanent_target_schema_is_closed(target_schema)
    ):
        return ()
    assert target_schema is not None
    DirectPermanentTargetSpec.from_target_schema(target_schema)
    dependencies = {
        TEMPORARY_TARGET_INTERACTION_CAPABILITY,
        "target.revalidate_resolution",
        *direct_target_predicate_capabilities(target_schema),
    }
    seen_keyword_grants: set[tuple[str, str]] = set()
    for effect in effects:
        operation = effect.get("op")
        condition = effect.get("target_condition")
        if not _condition(condition):
            return ()
        condition_field = {"target_condition"} if condition is not None else set()
        if operation == "modify_stats_until_end_of_turn":
            if (
                set(effect)
                != {"op", "card", "power", "toughness"} | condition_field
                or effect.get("card") != "$target.0"
                or any(
                    not (
                        type(value) is int
                        or (type(value) is str and value in {"$x", "$neg_x"})
                    )
                    for value in (effect.get("power"), effect.get("toughness"))
                )
            ):
                return ()
            dependencies.add(
                "continuous.resolution.fixed_characteristics_until_end_of_turn"
            )
            continue
        if operation == "grant_keyword_until_end_of_turn":
            fragment = effect.get("ability_fragment")
            fragment_field = {"ability_fragment"} if fragment is not None else set()
            keyword = effect.get("keyword")
            identity = (str(keyword), str(condition))
            if (
                set(effect)
                != {"op", "card", "keyword"} | condition_field | fragment_field
                or effect.get("card") != "$target.0"
                or type(keyword) is not str or keyword not in _KEYWORDS
                or identity in seen_keyword_grants
                or (fragment is not None) != (keyword == "Protection")
                or (fragment is not None and not _protection_fragment(fragment))
            ):
                return ()
            seen_keyword_grants.add(identity)
            dependencies.add(
                "continuous.resolution.fixed_characteristics_until_end_of_turn"
            )
            if keyword == "Protection":
                dependencies.add("protection.typed.debt")
            continue
        if operation == "choose_option":
            options = effect.get("options")
            branches = effect.get("then_by_choice")
            if (
                set(effect)
                != {"op", "player", "prompt", "options", "then_by_choice"}
                or effect.get("player")
                not in {"$controller", "$target.current_controller.0"}
                or not isinstance(options, (list, tuple))
                or not isinstance(branches, Mapping)
                or not isinstance(effect.get("prompt"), str)
                or not effect.get("prompt")
            ):
                return ()
            identities = [
                option.get("id") for option in options
                if isinstance(option, Mapping) and set(option) == {"id", "label"}
                and type(option.get("label")) is str and option.get("label")
            ]
            if (
                len(identities) != len(options)
                or any(type(value) is not str for value in identities)
                or len(set(identities)) != len(identities)
                or set(identities) != set(branches)
                or not (
                    set(identities) == {"W", "U", "B", "R", "G"}
                    or (len(identities) == 2 and set(identities) <= _KEYWORDS - {"Protection"})
                )
                or (len(identities) == 2 and effect.get("player") != "$controller")
            ):
                return ()
            for identity, branch in branches.items():
                if not isinstance(branch, (list, tuple)) or len(branch) != 1:
                    return ()
                grant = branch[0]
                if (
                    not isinstance(grant, Mapping)
                    or grant.get("op") != "grant_keyword_until_end_of_turn"
                    or grant.get("keyword") != (
                        "Protection" if identity in "WUBRG" else identity
                    )
                ):
                    return ()
                if identity in "WUBRG":
                    try:
                        fragment = ability_fragment_from_dict(grant.get("ability_fragment"))
                    except (TypeError, ValueError):
                        return ()
                    if not isinstance(fragment, ProtectionSpec) or fragment.quality != identity:
                        return ()
                owned = temporary_target_interaction_node_capabilities(
                    effects=branch, target_schema=target_schema, mechanic_ids=mechanics
                )
                if not owned:
                    return ()
                dependencies.update(owned)
            continue
        if operation == "grant_declaration_restriction_until_end_of_turn":
            if (
                set(effect) != {"op", "card", "restriction"}
                or effect.get("card") != "$target.0"
                or effect.get("restriction") != "unblockable"
            ):
                return ()
            dependencies.update(
                {
                    "combat.declaration.typed_components",
                    "continuous.resolution.declaration_rules_until_end_of_turn",
                }
            )
            continue
        if operation == "regenerate":
            if set(effect) != {"op", "card"} or effect.get("card") != "$target.0":
                return ()
            dependencies.add("permanent.regeneration.fixed_effect")
            continue
        return ()
    return tuple(sorted(dependencies))


__all__ = ["temporary_target_interaction_node_capabilities"]
