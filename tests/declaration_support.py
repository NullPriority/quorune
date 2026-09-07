from __future__ import annotations

"""Typed combat-declaration fixtures for custom test permanents."""

from quorune.ability_fragments import ability_fragment_to_dict
from quorune.declaration_costs import parse_declaration_cost_line
from quorune.declaration_requirements import (
    parse_declaration_requirement_line,
)
from quorune.declaration_restrictions import (
    parse_declaration_restriction_line,
)
from quorune.compiler.declaration_nodes import (
    fixed_declaration_fragment_sequence,
)


def compiled_declaration_fragments(
    card_name: str,
    oracle_text: str,
) -> list[dict[str, object]]:
    """Compile only exact declaration fixture lines before game execution."""

    fragments: list[dict[str, object]] = []
    for line in str(oracle_text).splitlines():
        sequence = fixed_declaration_fragment_sequence(
            line,
            card_name=card_name,
        )
        if len(sequence) > 1:
            fragments.extend(
                ability_fragment_to_dict(fragment) for fragment in sequence
            )
            continue
        requirement = parse_declaration_requirement_line(
            line,
            card_name=card_name,
        )
        if requirement is not None:
            fragments.append(ability_fragment_to_dict(requirement))
            continue
        cost = parse_declaration_cost_line(line, card_name=card_name)
        if cost.exact and cost.template is not None:
            fragments.append(ability_fragment_to_dict(cost.template))
            continue
        restriction = parse_declaration_restriction_line(
            line,
            card_name=card_name,
        )
        if restriction.exact and restriction.template is not None:
            fragments.append(
                ability_fragment_to_dict(restriction.template)
            )
    return fragments


__all__ = ["compiled_declaration_fragments"]
