from __future__ import annotations

"""Closed fixed single-object zone-change costs for activated abilities."""

from dataclasses import replace
import re

from ..abilities import ActivatedAbility, CostChoice
from ..additional_cost_vocabulary import (
    DISCARD_ONE_COST,
    FIXED_ZONE_CHANGE_COST_CONTRACTS,
    RETURN_ONE_TO_OWNER_HAND_COST,
    SACRIFICE_ONE_COST,
)
from ..replacement.immutable import FrozenMap
from .spell_additional_cost_templates import (
    fixed_sacrifice_additional_cost_template,
    fixed_zone_change_additional_cost_template,
)


_ANOTHER_SACRIFICE = re.compile(
    r"^sacrifice another (?P<quality>[A-Za-z][A-Za-z -]*)$",
    re.IGNORECASE,
)


def _activation_cost_clause(
    cost_text: str,
) -> tuple[str, bool]:
    """Normalize activation-only ownership and source-exclusion grammar."""

    clause = " ".join(cost_text.strip().removesuffix(".").split())
    if clause.casefold().startswith("sacrifice "):
        clause = re.sub(
            r"\s+you control$", "", clause, flags=re.IGNORECASE
        )
    match = _ANOTHER_SACRIFICE.fullmatch(clause)
    if match is None:
        return clause, False
    quality = match.group("quality")
    article = "an" if quality[0].casefold() in "aeiou" else "a"
    return f"Sacrifice {article} {quality}", True


def _zone_change_descriptor(cost_text: str) -> dict[str, object] | None:
    cost_clause, another = _activation_cost_clause(cost_text)
    clause = (
        "As an additional cost to cast this spell, "
        + cost_clause
        + "."
    )
    sacrifice = fixed_sacrifice_additional_cost_template(clause)
    if sacrifice is not None:
        return {
            "operation": SACRIFICE_ONE_COST,
            "predicate": dict(sacrifice.descriptor["predicate"]),
            "another": another,
        }
    template = fixed_zone_change_additional_cost_template(clause)
    if template is not None:
        return {**dict(template.descriptor), "another": another}
    return None


def fixed_activated_zone_change_cost(
    ability: ActivatedAbility,
) -> ActivatedAbility:
    """Lower one independently closed selected-object cost on an activation."""

    if (
        ability.mana_ability
        or ability.complex_symbols
        or len(ability.choices) > 1
        or ability.discard_source
        or ability.sacrifice_source
        or ability.exile_source
    ):
        return ability
    legacy_choice = ability.choices[0] if ability.choices else None
    if legacy_choice is not None:
        if (
            legacy_choice.predicate is not None
            or legacy_choice.count != 1
            or ability.uncompiled_costs
        ):
            return ability
        descriptors = tuple(
            descriptor
            for clause in ability.cost_text.split(",")
            if (descriptor := _zone_change_descriptor(clause)) is not None
        )
        descriptor = descriptors[0] if len(descriptors) == 1 else None
    else:
        descriptor = (
            _zone_change_descriptor(ability.uncompiled_costs[0])
            if len(ability.uncompiled_costs) == 1
            else None
        )
    if descriptor is None:
        return ability
    operation = str(descriptor["operation"])
    another = descriptor.get("another") is True
    contract = FIXED_ZONE_CHANGE_COST_CONTRACTS.get(operation)
    predicate = descriptor.get("predicate")
    if contract is None or not isinstance(predicate, dict):
        return ability
    if legacy_choice is not None and (
        legacy_choice.kind
        != {
            DISCARD_ONE_COST: "discard",
            RETURN_ONE_TO_OWNER_HAND_COST: "return",
            SACRIFICE_ONE_COST: "sacrifice",
        }.get(operation)
        or legacy_choice.zone != contract[0]
    ):
        return ability
    return replace(
        ability,
        choices=(
            CostChoice(
                kind=operation,
                zone=contract[0],
                another=another,
                predicate=FrozenMap(predicate),
            ),
        ),
        uncompiled_costs=(),
    )


__all__ = ["fixed_activated_zone_change_cost"]
