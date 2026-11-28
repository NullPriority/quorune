from __future__ import annotations

"""Runtime evaluation for declaration conditions outside battlefield counts."""

from typing import Any, Mapping, Protocol

from .continuous_conditions import FixedPublicStateConditionSpec
from .declaration_fragments import (
    DEFENDING_PLAYER_RAD_BLOCK_CAPABILITY,
    DEFENDING_PLAYER_RAD_BLOCK_TEMPLATE,
    DeclarationCondition,
    DeclarationPlayerStateCondition,
    DeclarationSourceStatCondition,
)


class DeclarationConditionRuntimeHost(Protocol):
    state: Any

    def _fixed_public_state_condition_holds(
        self,
        source: Any,
        condition: FixedPublicStateConditionSpec,
    ) -> bool: ...

    def _numeric_stat(self, object_id: str, stat: str) -> int: ...

    def _defending_player_for_attacker(self, attacker_id: str, target: str) -> str | None: ...

    def _declaration_condition_player(
        self,
        role: str,
        *,
        kind: str,
        source: Any,
        variable: str,
        option: str,
        by_ref: Mapping[str, Any],
    ) -> str | None: ...


def fixed_declaration_condition_verdict(
    host: DeclarationConditionRuntimeHost,
    condition: DeclarationCondition,
    *,
    kind: str,
    source: Any,
    variable: str,
    option: str,
    by_ref: Mapping[str, Any],
) -> bool | None:
    """Return a verdict for shared/source conditions or ``None`` otherwise."""

    if isinstance(condition, FixedPublicStateConditionSpec):
        return host._fixed_public_state_condition_holds(source, condition)
    if isinstance(condition, DeclarationSourceStatCondition):
        current = host._numeric_stat(source.object_id, condition.stat)
        return {
            "eq": current == condition.value,
            "lt": current < condition.value,
            "le": current <= condition.value,
            "gt": current > condition.value,
            "ge": current >= condition.value,
        }[condition.operator]
    if not isinstance(condition, DeclarationPlayerStateCondition):
        return None
    if condition.state == "rad_counter":
        return defending_player_has_rad_counter(host, kind=kind, option=option, by_ref=by_ref)
    player = host._declaration_condition_player(
        condition.player,
        kind=kind,
        source=source,
        variable=variable,
        option=option,
        by_ref=by_ref,
    )
    if player is None:
        return False
    if condition.state == "monarch":
        return host.state.monarch == player
    return host.state.players[player].poison > 0


def defending_player_has_rad_counter(host, *, kind, option, by_ref):
    from .counter_state import player_counter_snapshot
    from .rad_counter_model import RAD_COUNTER

    attacker = by_ref.get(option)
    if kind != "block" or attacker is None:
        return False
    target = host.state.combat.attackers.get(attacker.object_id)
    if target is None:
        return False
    defender = host._defending_player_for_attacker(attacker.object_id, target)
    return defender is not None and player_counter_snapshot(host.state.players[defender]).get(RAD_COUNTER, 0) > 0


def defending_player_rad_block_dependencies(program):
    from .semantic_runtime.ability_fragments import (
        DECLARATION_RESTRICTION_FRAGMENT_HANDLER_ID, DeclarationRestrictionAbilityFragmentHandler,
    )

    def is_scoped(envelope):
        fragment = envelope.get("value") if isinstance(envelope, Mapping) else None
        return isinstance(fragment, Mapping) and (
            fragment.get("template_id") == DEFENDING_PLAYER_RAD_BLOCK_TEMPLATE
            or (isinstance(fragment.get("condition"), Mapping) and fragment["condition"].get("state") == "rad_counter")
        )

    scoped = tuple(handler for handler in program.handlers if is_scoped(handler.get("fragment")))
    if not scoped and DEFENDING_PLAYER_RAD_BLOCK_CAPABILITY not in program.capability_dependencies:
        return ()
    if (len(scoped) != 1 or len(program.handlers) != 1 or program.effects or program.target_schema is not None
            or program.event_condition is not None or (program.event, program.active_zone) != ("combat.declaration", "battlefield")
            or scoped[0].get("handler_id") != DECLARATION_RESTRICTION_FRAGMENT_HANDLER_ID):
        raise ValueError("Defending-player rad blocking requires its original static declaration restriction")
    if DeclarationRestrictionAbilityFragmentHandler().validate(scoped[0]).template_id != DEFENDING_PLAYER_RAD_BLOCK_TEMPLATE:
        raise ValueError("Defending-player rad lost its intrinsic blocker restriction")
    return (DEFENDING_PLAYER_RAD_BLOCK_CAPABILITY,)


__all__ = [
    "DeclarationConditionRuntimeHost",
    "fixed_declaration_condition_verdict",
]
