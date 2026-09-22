from __future__ import annotations

"""Runtime projection of typed declaration requirement fragments."""

from typing import Any, Mapping, Sequence

from .ability_fragments import declaration_requirement_specs
from .combat_constraints import DeclarationRequirement
from .model import CardInstance
from .combat_entry_activations import encore_attack_requirement


def typed_attacker_requirements(
    host: Any,
    attacker: CardInstance,
    *,
    active_seats: Sequence[str],
    error_type: type[Exception],
) -> list[DeclarationRequirement]:
    """Return current intrinsic attack requirements for one legal attacker."""

    result: list[DeclarationRequirement] = []
    for index, requirement in enumerate(
        declaration_requirement_specs(
            host._effective_ability_fragments(attacker, error_type=error_type)
        )
    ):
        if requirement.kind != "attack_each_combat":
            continue
        result.append(
            DeclarationRequirement(
                requirement_id=f"attack:{attacker.ref}:each-combat:{index}",
                kind="choose",
                variable=attacker.ref,
                label=(
                    f"{host.display_name(attacker.object_id)} attacks this "
                    "combat if able."
                ),
            )
        )
    encore_opponent = encore_attack_requirement(attacker)
    if encore_opponent in active_seats:
        result.append(
            DeclarationRequirement(
                requirement_id=(
                    f"attack:{attacker.ref}:encore:{encore_opponent}"
                ),
                kind="choose_option",
                variable=attacker.ref,
                option=encore_opponent,
                label=(
                    f"{host.display_name(attacker.object_id)} attacks "
                    f"{encore_opponent} this combat if able."
                ),
            )
        )
    return result


def typed_blocker_requirements(
    host: Any,
    blockers_by_ref: Mapping[str, CardInstance],
    *,
    error_type: type[Exception],
) -> list[DeclarationRequirement]:
    result: list[DeclarationRequirement] = []
    for blocker_ref, blocker in blockers_by_ref.items():
        fragments = host._effective_ability_fragments(
            blocker,
            error_type=error_type,
        )
        for index, requirement in enumerate(
            declaration_requirement_specs(fragments)
        ):
            if requirement.kind == "block_each_combat":
                result.append(
                    DeclarationRequirement(
                        requirement_id=(
                            f"block:{blocker_ref}:each-combat:{index}"
                        ),
                        kind="choose",
                        variable=blocker_ref,
                        label=(
                            f"{host.display_name(blocker.object_id)} blocks "
                            "this combat if able."
                        ),
                    )
                )
    return result


def typed_attacker_block_requirements(
    host: Any,
    attacker: CardInstance,
    domains: Mapping[str, Sequence[str]],
    blockers_by_ref: Mapping[str, CardInstance],
    *,
    error_type: type[Exception],
) -> list[DeclarationRequirement]:
    result: list[DeclarationRequirement] = []
    fragments = host._effective_ability_fragments(
        attacker,
        error_type=error_type,
    )
    for index, requirement in enumerate(
        declaration_requirement_specs(fragments)
    ):
        if requirement.kind == "must_be_blocked":
            result.append(
                DeclarationRequirement(
                    requirement_id=f"block:{attacker.ref}:if-able:{index}",
                    kind="option_used",
                    option=attacker.ref,
                    label=(
                        f"{host.display_name(attacker.object_id)} must be "
                        "blocked if able."
                    ),
                )
            )
            continue
        if requirement.kind != "all_able_blockers":
            continue
        for blocker_ref, legal in domains.items():
            if attacker.ref not in legal:
                continue
            blocker = blockers_by_ref[blocker_ref]
            result.append(
                DeclarationRequirement(
                    requirement_id=(
                        f"block:{blocker_ref}:{attacker.ref}:all:{index}"
                    ),
                    kind="choose_option",
                    variable=blocker_ref,
                    option=attacker.ref,
                    label=(
                        f"{host.display_name(blocker.object_id)} blocks "
                        f"{host.display_name(attacker.object_id)} if able."
                    ),
                )
            )
    return result


__all__ = [
    "typed_attacker_requirements",
    "typed_attacker_block_requirements",
    "typed_blocker_requirements",
]
