from __future__ import annotations

from typing import Any, Mapping, Protocol, Sequence

from .damage_prevention import DamageModifierCommitPlan, DamageModifierSnapshot
from .damage_values import DamageProposal, DamageRecipientSnapshot


class PreparedDamageTransaction(Protocol):
    """Narrow prepared-value view needed by nested damage producers."""

    consumed_selections: int
    modifier_plan: DamageModifierCommitPlan


class DamageTransactionResult(Protocol):
    """Narrow committed-result view returned to nested damage producers."""

    changed_players: tuple[str, ...]
    changed_objects: tuple[str, ...]

    @property
    def dealt_amount(self) -> int: ...


class DamageTransactionPort(Protocol):
    """Acyclic adapter to the one canonical damage transaction."""

    def with_counter_prevention(self, prepared: Sequence[Any]) -> "DamageTransactionPort": ...

    def recipient(
        self,
        ref: str,
        *,
        actor: str,
    ) -> DamageRecipientSnapshot: ...

    def prepare(
        self,
        proposals: Sequence[DamageProposal],
        *,
        selections: Sequence[str | None | Mapping[str, object]],
        sources: Sequence[object] | None,
        source_zones: Mapping[str, str] | None,
        modifier_snapshot: DamageModifierSnapshot,
        aftermath_depth: int,
        aftermath_effect_chain: tuple[str, ...],
    ) -> PreparedDamageTransaction: ...

    def commit(
        self,
        prepared: PreparedDamageTransaction,
    ) -> DamageTransactionResult: ...


__all__ = [
    "DamageTransactionPort",
    "DamageTransactionResult",
    "PreparedDamageTransaction",
]
