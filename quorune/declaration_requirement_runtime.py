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
    from .rules.bound_static_declarations import bound_declaration_requirements
    intrinsic=tuple(enumerate(
        declaration_requirement_specs(
            host._effective_ability_fragments(attacker, error_type=error_type)
        )
    ))
    for index, requirement in (*intrinsic,*bound_declaration_requirements(host,attacker,error_type=error_type)):
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
    from .rules.enchanted_player_requirements import enchanted_player_attacker_requirements
    result.extend(enchanted_player_attacker_requirements(host,attacker,error_type=error_type))
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
    for requirement in host.state.combat.blocking_requirements:
        attacker=host.state.cards.get(requirement.attacker.object_id)
        blocker=host.state.cards.get(requirement.blocker.object_id)
        if (attacker is None or blocker is None or attacker.zone!='battlefield' or blocker.zone!='battlefield'
                or attacker.logical_object_id!=requirement.attacker.logical_object_id
                or blocker.logical_object_id!=requirement.blocker.logical_object_id
                or attacker.object_id not in host.state.combat.attackers or attacker.phased_out or blocker.phased_out
                or blocker.ref not in blockers_by_ref):continue
        result.append(DeclarationRequirement(requirement_id=requirement.requirement_id,kind='choose_option',
            variable=blocker.ref,option=attacker.ref,label='This creature blocks the required attacker this combat if able.'))
    for blocker_ref, blocker in blockers_by_ref.items():
        fragments = host._effective_ability_fragments(
            blocker,
            error_type=error_type,
        )
        from .rules.bound_static_declarations import bound_declaration_requirements
        for index, requirement in (*enumerate(declaration_requirement_specs(fragments)),
                *bound_declaration_requirements(host,blocker,error_type=error_type)):
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
    from .rules.all_blocker_requirements import current_attacker_block_requirements
    for index, requirement in current_attacker_block_requirements(host,attacker,error_type=error_type):
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
