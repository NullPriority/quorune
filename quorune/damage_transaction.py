from __future__ import annotations

from typing import Any, Mapping, Protocol, Sequence, TYPE_CHECKING

from .damage_source import DamageError
from .replacement.ordering import ReplacementChoiceRequired

if TYPE_CHECKING:
    from .damage_prevention import DamageModifierCommitPlan, DamageModifierSnapshot
    from .damage_prevention_aftermath import PreparedPreventionAftermath
    from .damage_results import PreparedDamageResults
    from .damage_values import DamageProposal, DamageRecipientSnapshot


class DamageReplacementChoiceRequired(ReplacementChoiceRequired):
    """Pin the original damage proposals while a derived event is suspended."""

    def __init__(self, required: ReplacementChoiceRequired, proposal_ids: tuple[str, ...]) -> None:
        super().__init__(batch=required.batch, effects=required.effects, pending=required.pending)
        self.proposal_ids = proposal_ids


def damage_proposal_continuation_fields(required: ReplacementChoiceRequired) -> dict[str, list[str]]:
    if isinstance(required, DamageReplacementChoiceRequired):
        return {"damage_proposal_ids": list(required.proposal_ids)}
    return {}


class PreparedDamageTransaction(Protocol):
    """Narrow prepared-value view needed by nested damage producers."""

    consumed_selections: int
    modifier_plan: DamageModifierCommitPlan
    aftermath: PreparedPreventionAftermath


class DamageTransactionResult(Protocol):
    """Narrow committed-result view returned to nested damage producers."""

    changed_players: tuple[str, ...]
    changed_objects: tuple[str, ...]

    @property
    def dealt_amount(self) -> int: ...


class DamageTransactionPort(Protocol):
    """Acyclic adapter to the one canonical damage transaction."""

    def with_counter_prevention(self, prepared: Sequence[Any]) -> "DamageTransactionPort": ...

    def with_damage_results(
        self,
        results: PreparedDamageResults,
        modifiers: DamageModifierSnapshot,
    ) -> "DamageTransactionPort": ...

    def with_prepared_damage(self, prepared: PreparedDamageTransaction) -> "DamageTransactionPort": ...

    def modifier_snapshot(self) -> DamageModifierSnapshot: ...

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
        require_all_selections: bool = True,
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


def validate_damage_replacement_journals(prepared) -> None:
    """Verify both ordered replacement journals against their prepared snapshots."""
    _validate_replacement_journal(
        prepared.effects, prepared.events, prepared.journal,
        "Damage replacement journal does not match its snapshot",
    )
    _validate_replacement_journal(
        prepared.result_effects, prepared.result_events, prepared.result_journal,
        "Damage-result replacement journal does not match its snapshot",
    )


def _validate_replacement_journal(effects, events, journal, error_message):
    effect_ids = {effect.effect_id for effect in effects}
    event_ids = {event.event_id for event in events}
    for selection in journal:
        selected_id = str(selection.effect_id or "")
        if selected_id.startswith("decline:"):
            continue
        if selected_id not in effect_ids or selection.event_id not in event_ids:
            raise DamageError(error_message)
