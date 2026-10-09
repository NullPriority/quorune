from __future__ import annotations

"""Public target occurrences sealed by authoritative stack target commits."""

from dataclasses import dataclass
from typing import Any, Mapping


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
    trigger_batch: list[Any] | None = None,
) -> None:
    """A stack object targets each new current permanent once per occurrence."""
    previous = set(previous_targets)
    for ref in dict.fromkeys(item.targets):
        if ref in previous:
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
