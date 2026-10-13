from __future__ import annotations

"""Conditional return movement over the canonical simultaneous zone owner.

The caller supplies the committed specified departure events. This boundary is
independent of triggered-ability placement. Native source-departure hooks and
their enclosing command/resolution continuations remain a separate obligation.
"""

from typing import Any, Mapping, Sequence

from .conditional_exile_model import (
    ConditionalExileBinding, conditional_departure_return_objects, conditional_source_remains,
)
from .continuous_effect_model import ContinuousObjectIdentity
from .errors import GameRuleError
from .zone_transitions import ZoneTransitionOwner


def return_after_source_departures(
    host: Any, bindings: Sequence[ConditionalExileBinding],
    departed_sources: Sequence[ContinuousObjectIdentity], *,
    replacement_selections: Sequence[str | None | Mapping[str, Any]] = (),
) -> tuple[Any, ...]:
    """Commit the second one-shot effect after actual specified departures."""
    try:
        cards = conditional_departure_return_objects(bindings, departed_sources, host.state.cards)
    except ValueError as exc:
        raise GameRuleError(str(exc)) from exc
    if any(conditional_source_remains(source, host.state.cards) for source in departed_sources):
        raise GameRuleError('Conditional return requires a committed source departure')
    if not cards:
        return ()
    controllers = {card.object_id: card.owner for card in cards}
    if any(owner not in host.active_seats for owner in controllers.values()):
        raise GameRuleError('Conditional return owner is unavailable')
    return tuple(ZoneTransitionOwner(host).move_cards_simultaneously(
        tuple((card.object_id, 'battlefield') for card in cards),
        reason='conditional exile source left the battlefield',
        destination_controllers=controllers, replacement_selections=replacement_selections,
    ))
