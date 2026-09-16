from __future__ import annotations

"""Runtime evaluation for declaration conditions outside battlefield counts."""

from typing import Any, Mapping, Protocol

from .continuous_conditions import FixedPublicStateConditionSpec
from .declaration_fragments import (
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


__all__ = [
    "DeclarationConditionRuntimeHost",
    "fixed_declaration_condition_verdict",
]
