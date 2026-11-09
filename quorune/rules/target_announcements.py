from __future__ import annotations

"""Public target occurrences sealed by authoritative stack target commits."""

from dataclasses import dataclass
from typing import Any, Mapping
from ..selection.target_query import target_identity_matches_snapshot


TARGET_ANNOUNCEMENT_CAPABILITY = 'trigger.event.normalized_target_announcement'
TARGET_ANNOUNCEMENT_EVENT = 'object.became_target'


@dataclass(frozen=True, slots=True)
class TargetAnnouncement:
    stack_ref: str
    stack_controller: str
    stack_kind: str
    target_ref: str
    target_controller: str
    target_logical_object_id: str
    types: tuple[str, ...]

    def __post_init__(self) -> None:
        if any(type(value) is not str or not value for value in (
            self.stack_ref, self.stack_controller, self.stack_kind, self.target_ref,
            self.target_controller, self.target_logical_object_id,
        )) or self.stack_kind not in {'spell', 'spell_copy', 'activated_ability', 'triggered_ability'}:
            raise ValueError('Target announcement requires a closed stack and target identity')

    def to_context(self) -> dict[str, Any]:
        return {
            'schema_version': 1, 'stack': self.stack_ref,
            'stack_controller': self.stack_controller, 'stack_kind': self.stack_kind,
            'card': self.target_ref, 'controller': self.target_controller,
            'card_object_identity': self.target_logical_object_id, 'types': list(self.types),
        }


def dispatch_target_announcements(
    host: Any, item: Any, *, previous_targets: tuple[str, ...] = (),
    previous_target_snapshots: Mapping[str, Any] | None = None,
    trigger_batch: list[Any] | None = None,
) -> None:
    """A stack object targets each new current permanent once per occurrence."""
    previous = set(previous_targets)
    snapshots = item.context.get('target_snapshots', {})
    for ref in dict.fromkeys(item.targets):
        snapshot = snapshots.get(ref) if isinstance(snapshots, Mapping) else None
        if not isinstance(snapshot, Mapping) or not target_identity_matches_snapshot(host, ref, snapshot):
            continue
        if ref in previous:
            if previous_target_snapshots is None:
                continue
            old = previous_target_snapshots.get(ref)
            if isinstance(old, Mapping) and target_identity_matches_snapshot(host, ref, old):
                continue
        card = next((card for card in host.state.cards.values() if card.ref == ref and
            card.zone == 'battlefield' and not card.phased_out), None)
        if card is None:
            continue
        types, _, _ = host._type_parts(str(host._effective_card_data(card).get('type_line') or ''))
        occurrence = TargetAnnouncement(
            item.ref, item.controller, item.kind, card.ref, card.controller,
            card.logical_object_id, tuple(sorted(types)),
        )
        host._dispatch_semantic_event(TARGET_ANNOUNCEMENT_EVENT, occurrence.to_context(), trigger_batch=trigger_batch)


def _detects_targets_before_payment(host: Any) -> bool:
    return getattr(host.state, 'target_announcement_timing_version', None) == 1


def announce_initial_target_triggers(host: Any, item: Any | None) -> bool:
    """Detect initial targeting while the announced battlefield objects are current."""
    if item is None or not _detects_targets_before_payment(host):
        return False
    from ..trigger_processing import collect_ward_occurrences

    dispatch_target_announcements(host, item)
    collect_ward_occurrences(host, item)
    return True


def dispatch_legacy_paid_target_announcements(host: Any, item: Any, *, trigger_batch: list[Any]) -> None:
    if not _detects_targets_before_payment(host):
        dispatch_target_announcements(host, item, trigger_batch=trigger_batch)


def collect_legacy_paid_ward_occurrences(host: Any, item: Any) -> None:
    if not _detects_targets_before_payment(host):
        from ..trigger_processing import collect_ward_occurrences

        collect_ward_occurrences(host, item)
