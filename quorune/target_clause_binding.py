from __future__ import annotations

"""Pure target-role rebasing shared by compilation and admission."""

from typing import Any, Mapping


def scope_clause_target(value: Any, role: int, *, restore: bool = False) -> Any:
    if isinstance(value, Mapping):
        return {key: scope_clause_target(item, role, restore=restore) for key,item in value.items()}
    if isinstance(value, (list, tuple)):
        return [scope_clause_target(item, role, restore=restore) for item in value]
    group = '$target_group.clause_' + str(role)
    scalar = group + '.0'
    controller = group + '.current_controller.0'
    if restore:
        if value == controller:
            return '$target.current_controller.0'
        return '$target.0' if value == scalar else '$targets' if value == group else value
    if value == '$target.current_controller.0':
        return controller
    return scalar if value == '$target.0' else group if value == '$targets' else value


def scope_clause_effect(effect: Mapping[str, Any], role: int, *, restore: bool = False) -> dict[str, Any]:
    scoped = scope_clause_target(effect, role, restore=restore)
    branches = scoped.get('then_by_choice')
    if scoped.get('op') != 'choose_option' or not isinstance(branches, Mapping) or len(branches) != 2:
        return scoped
    reference = '$target.0' if restore else f'$target_group.clause_{role}.0'
    if not all(isinstance(branch, (list, tuple)) and len(branch) == 1
               and isinstance(branch[0], Mapping)
               and branch[0].get('op') == 'grant_keyword_until_end_of_turn'
               and branch[0].get('card') == reference for branch in branches.values()):
        return scoped
    # The option only applies its own target's keyword. Expose that target to
    # the existing instruction filter before the player is asked to choose.
    if restore and scoped.get('target') == reference:
        del scoped['target']
    elif not restore and 'target' not in scoped:
        scoped['target'] = reference
    return scoped


def clause_target_roles_are_exact(value: Any, role: int) -> bool:
    if isinstance(value, Mapping):
        return all(clause_target_roles_are_exact(item, role) for item in value.values())
    if isinstance(value, (list, tuple)):
        return all(clause_target_roles_are_exact(item, role) for item in value)
    if isinstance(value, str) and (value.startswith('$target.') or value == '$targets' or value.startswith('$target_group.')):
        group = '$target_group.clause_' + str(role)
        return value in {group, group + '.0', group + '.current_controller.0'}
    return True
