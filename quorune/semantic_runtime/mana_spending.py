from __future__ import annotations

"""Current controller-scoped spending facts in the existing permission registry."""

from ..mana_restrictions import mana_restriction_allows
from ..mana_spending_model import ManaSpendingMode, ManaSpendingPolicy
from .action_permissions import ActionPermissionKind, StaticActionPermissionHandler, controller_action_permissions

MANA_SPENDING_CAPABILITY = 'mana.spending.as_though'
MANA_SPENDING_RULES = ('106.1a','106.1b','107.4b','107.4c','107.4h','118.14','609.4','609.4b','611.3b')
MANA_SPENDING_HANDLERS = (
    ('permission.action.mana-white-as-red.v1', ActionPermissionKind.MANA_WHITE_AS_RED),
    ('permission.action.mana-any-color.v1', ActionPermissionKind.MANA_ANY_COLOR),
    ('permission.action.mana-any-type-creature.v1', ActionPermissionKind.MANA_ANY_TYPE_CREATURE),
)


def mana_spending_handlers():
    return tuple(StaticActionPermissionHandler(handler_id=handler,permission=kind,
        rule_references=MANA_SPENDING_RULES,capability_dependencies=(MANA_SPENDING_CAPABILITY,))
        for handler,kind in MANA_SPENDING_HANDLERS)


def current_mana_spending_policy(host, player: str, spend_context: str | None) -> ManaSpendingPolicy | None:
    modes=set()
    for permission in controller_action_permissions(host,player):
        if permission.kind is ActionPermissionKind.MANA_WHITE_AS_RED:modes.add(ManaSpendingMode.WHITE_AS_RED)
        elif permission.kind is ActionPermissionKind.MANA_ANY_COLOR:modes.add(ManaSpendingMode.ANY_COLOR)
        elif (permission.kind is ActionPermissionKind.MANA_ANY_TYPE_CREATURE
              and mana_restriction_allows('creature_spell_only',spend_context)):
            modes.add(ManaSpendingMode.ANY_TYPE)
    return ManaSpendingPolicy(frozenset(modes)) if modes else None
