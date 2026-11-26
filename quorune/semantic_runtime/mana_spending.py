from __future__ import annotations

"""Current controller-scoped spending facts in the existing permission registry."""

from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from ..mana_restrictions import exiled_spell_any_type_payer, exile_play_mana_mode, mana_restriction_allows
from ..mana_spending_model import ManaSpendingMode, ManaSpendingPolicy
from .action_permissions import (
    ACTION_PERMISSION_EVENT, ActionPermissionKind, StaticActionPermissionHandler, controller_action_permissions,
)

MANA_SPENDING_CAPABILITY = 'mana.spending.as_though'
MANA_SPENDING_RULES = ('106.1a','106.1b','107.4b','107.4c','107.4h','118.14','609.4','609.4b','611.3b')
MANA_SPENDING_HANDLERS = (
    ('permission.action.mana-white-as-red.v1', ActionPermissionKind.MANA_WHITE_AS_RED),
    ('permission.action.mana-any-color.v1', ActionPermissionKind.MANA_ANY_COLOR),
    ('permission.action.mana-any-type-creature.v1', ActionPermissionKind.MANA_ANY_TYPE_CREATURE),
)


TAP_MANA_PRODUCTION_MULTIPLIER_CAPABILITY = "mana.production.tap_multiplier"
TAP_MANA_PRODUCTION_MULTIPLIER_RULES = ("106.6a", "106.12", "106.12b", "605.1a", "614.1a", "614.5")
TAP_MANA_PRODUCTION_MULTIPLIERS = {
    ActionPermissionKind.DOUBLE_TAP_MANA: ("permission.action.double-tap-mana.v1", 2, False),
    ActionPermissionKind.TRIPLE_TAP_MANA: ("permission.action.triple-tap-mana.v1", 3, False),
    ActionPermissionKind.TRIPLE_BASIC_LAND_TAP_MANA: ("permission.action.triple-basic-land-tap-mana.v1", 3, True),
}


def tap_mana_multiplier_handlers():
    return tuple(
        StaticActionPermissionHandler(
            handler_id=handler_id, permission=kind, rule_references=TAP_MANA_PRODUCTION_MULTIPLIER_RULES,
            capability_dependencies=(TAP_MANA_PRODUCTION_MULTIPLIER_CAPABILITY,),
        )
        for kind, (handler_id, _factor, _basics_only) in TAP_MANA_PRODUCTION_MULTIPLIERS.items()
    )


def _is_tap_mana_multiplier_descriptor(descriptor):
    permission = descriptor.get("permission")
    return type(permission) is str and permission in TAP_MANA_PRODUCTION_MULTIPLIERS


def has_tap_mana_multiplier_program(host):
    semantics = getattr(host, "semantics", None)
    if semantics is None:
        return False
    return any(
        _is_tap_mana_multiplier_descriptor(descriptor)
        for program in semantics.programs_for_event(ACTION_PERMISSION_EVENT)
        for descriptor in program.handlers
    )


def tap_mana_multiplier_runtime_dependencies(program):
    candidates = tuple(descriptor for descriptor in program.handlers
                       if _is_tap_mana_multiplier_descriptor(descriptor))
    if not candidates:
        return ()
    if ((program.event, program.active_zone) != (ACTION_PERMISSION_EVENT, "battlefield")
            or program.effects or program.target_schema is not None or program.cost_schema is not None
            or program.event_condition is not None or len(candidates) != len(program.handlers)
            or CURRENT_ABILITY_FRAGMENT_COVERAGE not in program.coverage):
        raise ValueError("Tap-for-mana multipliers require their original battlefield permission")
    for descriptor in candidates:
        expected_id = TAP_MANA_PRODUCTION_MULTIPLIERS[descriptor["permission"]][0]
        if descriptor.get("handler_id") != expected_id:
            raise ValueError("Tap-for-mana multiplier kind must match its exact handler")
        handler = next(entry for entry in tap_mana_multiplier_handlers() if entry.handler_id == expected_id)
        handler.validate(descriptor)
    return (TAP_MANA_PRODUCTION_MULTIPLIER_CAPABILITY,)


def mana_spending_handlers():
    return tuple(StaticActionPermissionHandler(handler_id=handler,permission=kind,
        rule_references=MANA_SPENDING_RULES,capability_dependencies=(MANA_SPENDING_CAPABILITY,))
        for handler,kind in MANA_SPENDING_HANDLERS)


def current_mana_spending_policy(host, player: str, spend_context: str | None) -> ManaSpendingPolicy | None:
    from ..mana_restrictions import actual_ability_payment_color

    if actual_ability_payment_color(spend_context) is not None:
        return None
    modes=set()
    payer = exiled_spell_any_type_payer(spend_context)
    if payer is not None and payer == player:
        modes.add(ManaSpendingMode.ANY_TYPE)
    exile_mode = exile_play_mana_mode(spend_context, player)
    if exile_mode is not None:
        modes.add(exile_mode)
    for permission in controller_action_permissions(host,player):
        if permission.kind is ActionPermissionKind.MANA_WHITE_AS_RED:modes.add(ManaSpendingMode.WHITE_AS_RED)
        elif permission.kind is ActionPermissionKind.MANA_ANY_COLOR:modes.add(ManaSpendingMode.ANY_COLOR)
        elif (permission.kind is ActionPermissionKind.MANA_ANY_TYPE_CREATURE
              and mana_restriction_allows('creature_spell_only',spend_context)):
            modes.add(ManaSpendingMode.ANY_TYPE)
    return ManaSpendingPolicy(frozenset(modes)) if modes else None
