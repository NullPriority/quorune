"""Command-scoped observations of actual stack objects and physical sources."""

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping


@dataclass(frozen=True, slots=True)
class StackResolutionInputs(Mapping[str, Any]):
    stack_items: Mapping[str, Any]
    physical_cards: Mapping[str, Any]

    def __getitem__(self, key):return self.stack_items[key]
    def __iter__(self):return iter(self.stack_items)
    def __len__(self):return len(self.stack_items)


@dataclass(slots=True)
class _ResolutionCapture:
    owner: object
    stack_items: dict[str, Any]
    physical_cards: dict[str, Any]

    def snapshot(self):
        return StackResolutionInputs(MappingProxyType(dict(self.stack_items)),
            MappingProxyType(dict(self.physical_cards)))


_current_capture = ContextVar('stack_resolution_capture', default=None)


@contextmanager
def capture_stack_resolutions(owner: object, initial: StackResolutionInputs):
    capture = _ResolutionCapture(owner, dict(initial.stack_items), dict(initial.physical_cards))
    token = _current_capture.set(capture)
    try:
        yield capture
    finally:
        _current_capture.reset(token)


def observe_stack_resolution(owner: object, item: Any, physical_card: Any) -> None:
    capture = _current_capture.get()
    if capture is None or capture.owner is not owner:
        return
    capture.stack_items[item.ref] = item
    if capture.physical_cards.get(item.ref) is None:
        capture.physical_cards[item.ref] = physical_card
