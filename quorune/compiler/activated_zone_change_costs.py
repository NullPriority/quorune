from __future__ import annotations

"""Closed fixed homogeneous zone-change costs for activated abilities."""

from dataclasses import replace
from . import regex_working_set as re

from ..abilities import ActivatedAbility, CostChoice
from ..additional_cost_vocabulary import (
    DISCARD_ONE_COST,
    FIXED_ZONE_CHANGE_COST_CONTRACTS,
    RETURN_ONE_TO_OWNER_HAND_COST,
    SACRIFICE_ONE_COST,
)
from ..replacement.immutable import FrozenMap
from ..sacrifice_union_model import (
    colored_or_artifact_creature_sacrifice, controlled_permanent_predicate,
)
from .spell_additional_cost_templates import (
    _COLOR_WORDS,
    fixed_sacrifice_additional_cost_template,
    fixed_zone_change_additional_cost_template,
)


_ANOTHER_SACRIFICE = re.compile(
    r"^sacrifice another (?P<quality>[A-Za-z][A-Za-z -]*)$",
    re.IGNORECASE,
)
_COUNTED_OTHER_SACRIFICE = re.compile(
    r'^sacrifice (?P<count>two|three|four|five|six|seven|eight|nine|ten|[2-9]|10) other (?P<quality>[A-Za-z][A-Za-z -]*)$', re.I,
)
_COLORED_OR_ARTIFACT_CREATURE_COST = re.compile(
    r"Sacrifice a (?P<color>white|blue|black|red|green) or artifact creature", re.I,
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
    counted_other = _COUNTED_OTHER_SACRIFICE.fullmatch(clause)
    if counted_other is not None:
        return f"Sacrifice {counted_other['count']} {counted_other['quality']}", True
    match = _ANOTHER_SACRIFICE.fullmatch(clause)
    if match is None:
        return clause, False
    quality = match.group("quality")
    article = "an" if quality[0].casefold() in "aeiou" else "a"
    return f"Sacrifice {article} {quality}", True


def _zone_change_descriptor(cost_text: str) -> dict[str, object] | None:
    cost_clause, another = _activation_cost_clause(cost_text)
    union_match = _COLORED_OR_ARTIFACT_CREATURE_COST.fullmatch(cost_clause)
    if union_match is not None:
        union = colored_or_artifact_creature_sacrifice(_COLOR_WORDS[union_match['color'].casefold()])
        return {
            "operation": SACRIFICE_ONE_COST,
            "predicate": controlled_permanent_predicate().to_dict(),
            "predicate_union": union.to_dict(),
            "another": another,
            "count": 1,
        }
    if re.fullmatch(r'Sacrifice a creature with defender',cost_clause,re.I):
        from ..keyword_sacrifice_cost_model import KEYWORD_SACRIFICE_COST_VERSION, keyword_sacrifice_predicate
        return {'operation':SACRIFICE_ONE_COST,'predicate':keyword_sacrifice_predicate('defender').to_dict(),
            'another':another,'count':1,'schema_version':KEYWORD_SACRIFICE_COST_VERSION}
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
            "another": another, "count": 1,
        }
    template = fixed_zone_change_additional_cost_template(clause, allow_counted=True)
    if template is not None:
        return {**dict(template.descriptor), "another": another}
    return None


def fixed_activated_zone_change_cost(
    ability: ActivatedAbility,
) -> ActivatedAbility:
    """Lower one independently closed selected-object cost on an activation."""

    if (
        len(ability.choices) > 1
        or ability.discard_source
        or ability.sacrifice_source
        or ability.exile_source
    ):
        return ability
    if not ability.choices and tuple(part.casefold() for part in ability.uncompiled_costs) == ("forage",):
        from ..rules.forage import FORAGE_COST_KIND, validate_forage_activation
        from ..rules.forage_mana import forage_mana_text

        if (forage_mana_text(ability.cost_text, ability.effect_text)
                or re.fullmatch(r"(?:\{[0-9]+\}|\{[WUBRGC]\})+,\s*Forage", ability.cost_text.strip(), re.I)):
            candidate = replace(ability, choices=(CostChoice(kind=FORAGE_COST_KIND),), uncompiled_costs=())
            try:
                validate_forage_activation(candidate)
            except ValueError:
                return ability
            return candidate
    legacy_choice = ability.choices[0] if ability.choices else None
    if legacy_choice is not None:
        if (
            legacy_choice.predicate is not None
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
    if ability.mana_ability and operation!=SACRIFICE_ONE_COST:return ability
    another = descriptor.get("another") is True
    contract = FIXED_ZONE_CHANGE_COST_CONTRACTS.get(operation)
    predicate = descriptor.get("predicate")
    union = descriptor.get("predicate_union")
    count = descriptor.get('count', 1)
    if contract is None or not isinstance(predicate, dict):
        return ability
    if union is not None:
        from .paid_sacrifice_amount_templates import has_paid_creature_mana_value_output

        if not ability.mana_ability or not has_paid_creature_mana_value_output(ability.effect_text):
            return ability
    if legacy_choice is not None and (
        legacy_choice.kind
        != {
            DISCARD_ONE_COST: "discard",
            RETURN_ONE_TO_OWNER_HAND_COST: "return",
            SACRIFICE_ONE_COST: "sacrifice",
        }.get(operation)
        or legacy_choice.zone != contract[0]
        or legacy_choice.count != count
    ):
        return ability
    return replace(
        ability,
        choices=(
            CostChoice(
                kind=operation,
                count=count,
                zone=contract[0],
                another=another,
                predicate=FrozenMap(predicate),
                predicate_union=FrozenMap(union) if isinstance(union, dict) else None,
                return_schema_version=(7 if descriptor.get('schema_version') == 7 else None),
                sacrifice_schema_version=(8 if descriptor.get('schema_version') == 8 else None),
            ),
        ),
        uncompiled_costs=(),
    )


__all__ = ["fixed_activated_zone_change_cost"]
